import contextlib
import functools
import json
import os.path
import pathlib
import subprocess
import threading
import webbrowser

import click
import yaml

import gen
from .checks import LinkChecker
from .core import Thingie
from .freeze import make_freezer


@click.group()
@click.option(
    '--project',
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default='.',
    show_default=True,
    help=f"Project root.",
)
@click.pass_context
def cli(ctx, project):
    ctx.obj = project


@cli.command()
@click.option('-h', '--host', default='localhost', help="The interface to bind to.")
@click.option('-p', '--port', default=8080, type=int, help="The port to bind to.")
@click.option('--open/--no-open', help="Open a browser.")
@click.pass_obj
def serve(project, host, port, open):
    from werkzeug.serving import run_simple
    from .app import create_app

    # TODO: threads, reload, debug
    url = f"http://{host}:{port}"
    app = create_app(project, project_url=url)
    open_fn = webbrowser.open if open else lambda url: None
    timer = threading.Timer(0.5, open_fn, (url,))
    try:
        timer.start()
        app.run(host, port)
    finally:
        timer.cancel()


@cli.command()
@click.argument(
    'outdir',
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    '-f',
    '--force/--no-force',
    help="Overwrite any previously generated files. "
    "WARNING: All other files will be deleted.",
)
@click.option(
    '--deploy/--no-deploy',
    default=None,
    help="If OUTDIR is a git repo, add all changed, commit, and push.",
)
@click.option('--cache/--no-cache', 'cache_option')
@click.option('-v', '--verbose', count=True)
@click.pass_obj
@click.pass_context
def freeze(ctx, project, outdir, force, deploy, cache_option, verbose):
    stdout_isatty = click.get_text_stream('stdout').isatty()
    confirm_overwrite = os.path.exists(outdir) and not force

    # TODO: all conversations should be on stderr

    if confirm_overwrite:
        if stdout_isatty:
            click.confirm(
                f"{click.style('WARNING', fg='red', bold=True)}: {outdir} exists. \n"
                "Previously generated files will be overwritten. \n"
                "All other files will be deleted.\n"
                "Proceed?",
                abort=True,
                err=True,
            )
        else:
            click.echo(f"{outdir} exists, but --force not passed.")
            raise click.Abort()

    content_root = os.path.join(project, 'content')
    thingie = Thingie(content_root)

    def log(*args):
        if verbose:
            click.echo(' '.join(map(str, args)))

    if cache_option:
        import diskcache

        cache = diskcache.Cache(os.path.join(project, '.gen/cache/data'))
        ctx.call_on_close(cache.close)
        invalidate_cache(project, thingie, cache)
        node_cache_decorator = make_node_cache_decorator(cache, log)

    else:
        node_cache_decorator = functools.lru_cache

    from .app import create_app
    from jinja2 import FileSystemBytecodeCache

    app = create_app(project, node_cache_decorator=node_cache_decorator)

    if cache_option:
        jinja_cache_path = os.path.join(project, '.gen/cache/jinja')
        os.makedirs(jinja_cache_path, exist_ok=True)
        app.jinja_env.bytecode_cache = FileSystemBytecodeCache(
            jinja_cache_path, '%s.cache'
        )

    app.config['GEN_FREEZING'] = True

    app.config['FREEZER_DESTINATION'] = outdir
    app.config['FREEZER_REDIRECT_POLICY'] = 'error'
    app.config['FREEZER_DESTINATION_IGNORE'] = ['.git*']

    # TODO: check for uncommited changes in outdir; also, probably pull

    freezer = make_freezer(app)

    rv = freezer.freeze_yield()
    if not verbose:
        progressbar = click.progressbar(
            rv,
            item_show_func=lambda p: p.url if p else 'Done!',
            show_pos=True,
        )
    else:
        progressbar = contextlib.nullcontext(rv)
    with progressbar as pages:
        for page in pages:
            log('done', page.path)

    # TODO: maybe FREEZER_IGNORE_404_NOT_FOUND, so we don't fail fast for broken links (and get a full error report later)?

    if errors := dict(app.extensions['state'].checker.check_all()):
        errors_str = yaml.safe_dump(errors)
        raise click.ClickException(f"Some checks failed:\n\n{errors_str}\n")

    # TODO: these should be per-freezer (it's only suitable for github pages)
    # TODO: maybe get the freezer to not clobber them

    with open(os.path.join(outdir, '.nojekyll'), 'w'):
        pass

    cname = thingie.get_page('index').meta.get('project-cname')
    if cname:
        with open(os.path.join(outdir, 'CNAME'), 'w') as f:
            f.write(cname + '\n')

    know_how_to_deploy = os.path.isdir(os.path.join(outdir, '.git'))
    if not know_how_to_deploy:
        click.echo("OUTDIR not a git repo, nothing to deploy.")
        return

    if deploy is None:
        if not stdout_isatty:
            click.echo("--deploy not passed, not deploying.")
            return
        deploy = click.confirm("Deploy?")
    if deploy:
        subprocess.run(['git', '-C', outdir, 'add', '--all'])
        subprocess.run(['git', '-C', outdir, 'commit', '-m', "deploy"])
        subprocess.run(['git', '-C', outdir, 'push'])


def make_node_cache_decorator(cache, log):
    def node_cache_decorator(fn):
        @functools.lru_cache
        @functools.wraps(fn)
        def wrapper(id, *args):
            key = (f'{fn.__module__}.{fn.__qualname__}', id) + args

            rv = cache.get(key)
            if rv is not None:
                log('hit ', *key)
                return rv

            log('miss', *key)

            rv = fn(id, *args)
            cache.set(key, rv, tag=f'node:{id}')
            return rv

        return wrapper

    return node_cache_decorator


def invalidate_cache(project, thingie, cache):
    content_root = os.path.join(project, 'content')

    old_mtimes = cache.get('mtimes', {})
    new_mtimes = {}

    def check_mtime(key, path, glob):
        old_mtime = old_mtimes.get(key, 0)
        new_mtime = old_mtime
        for path in pathlib.Path(path).glob(glob):
            new_mtime = max(new_mtime, path.stat().st_mtime)
        if new_mtime > old_mtime:
            new_mtimes[key] = new_mtime

    check_mtime('dir:gen', gen.__path__[0], '**/*.py')
    check_mtime('dir:templates', os.path.join(project, 'templates'), '**/*')

    for id, path in thingie.get_page_paths():
        check_mtime(f'node:{id}', content_root, path)

    if new_mtimes:
        if any(key.startswith('dir:') for key in new_mtimes):
            cache.clear(retry=True)
        else:
            for key in new_mtimes:
                assert key.startswith('node:'), key
                cache.evict(key, retry=False)

        mtimes = old_mtimes.copy()
        mtimes.update(new_mtimes)

        cache.set('mtimes', mtimes)


if __name__ == '__main__':
    cli()

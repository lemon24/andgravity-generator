import contextlib
import functools
import os.path
import subprocess
import threading
import urllib.parse
import webbrowser

import click
import yaml

from .caching import invalidate_cache
from .caching import make_node_cache_decorator
from .freeze import make_freezer
from .storage import Storage


@click.group()
@click.option(
    '--project',
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default='.',
    show_default=True,
    help="Project root.",
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
@click.argument('url')
@click.option('-s', '--sort', default='cumulative')
@click.option('--lines', type=int, default=40)
@click.pass_obj
@click.pass_context
def profile(ctx, project, url, sort, lines):
    from .app import create_app
    import cProfile, pstats  # noqa: E401

    app = create_app(project, project_url="http://localhost:8888")
    client = ctx.with_resource(app.test_client())

    stream = click.get_text_stream('stdout')

    for i in range(2):
        click.echo(f" RUN #{i} ".center(80, '=') + '\n')
        pr = cProfile.Profile()
        pr.runcall(client.get, url)
        ps = pstats.Stats(pr, stream=stream)
        ps.strip_dirs().sort_stats(sort).print_stats(lines)


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
    storage = Storage(content_root)

    def log(*args):
        if verbose:
            click.echo(' '.join(map(str, args)))

    if cache_option:
        import diskcache

        cache = diskcache.Cache(os.path.join(project, '.gen/cache/data'))
        ctx.call_on_close(cache.close)
        evicted_nodes = invalidate_cache(project, storage, cache)
        node_cache_decorator = make_node_cache_decorator(cache, log)
    else:
        node_cache_decorator = functools.lru_cache

    from .app import create_app, get_project_url
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

    with app.test_request_context():
        project_url = urllib.parse.urlparse(get_project_url())

    # TODO: these should probably be per-freezer (don't want them for "local HTML")
    app.config['SERVER_NAME'] = project_url.netloc
    app.config['APPLICATION_ROOT'] = project_url.path or '/'
    app.config['PREFERRED_URL_SCHEME'] = project_url.scheme

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

    if cache_option:
        # TODO: this logic should be handled by the cache invalidator
        dependencies = cache.get('dependencies', {})
        for id in evicted_nodes:
            dependencies.pop(id, None)
        new_dependencies = app.extensions['state'].dependency_tracker.dependencies
        for key, value in new_dependencies.items():
            # we only update dependencies for evicted nodes
            # (nodes that were not evicted are not re-rendered,
            # so dependencies will be wrong for them)
            dependencies.setdefault(key, value)
        cache.set('dependencies', dependencies)

    # TODO: maybe FREEZER_IGNORE_404_NOT_FOUND, so we don't fail fast for broken links
    # (and get a full error report later)

    if errors := dict(app.extensions['state'].checker.check_all()):
        errors_str = yaml.safe_dump(errors)
        raise click.ClickException(f"Some checks failed:\n\n{errors_str}\n")

    # TODO: these should be per-freezer (it's only suitable for github pages)
    # TODO: maybe get the freezer to not clobber them

    with open(os.path.join(outdir, '.nojekyll'), 'w'):
        pass

    cname = storage.get_page('index').meta.get('project-cname')
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


if __name__ == '__main__':
    cli()

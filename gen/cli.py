import json
import os.path
import subprocess
import threading
import webbrowser

import click
import yaml

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
    app = create_app(project, url)
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
@click.pass_obj
def freeze(project, outdir, force, deploy):
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

    thingie = Thingie(os.path.join(project, 'content'))
    project_url = thingie.get_page('index').meta['project-url']

    from .app import create_app

    app = create_app(
        project, project_url.rstrip('/'), enable_checks=False, cache_markdown=True
    )

    app.config['GEN_FREEZING'] = True

    app.config['FREEZER_DESTINATION'] = outdir
    app.config['FREEZER_REDIRECT_POLICY'] = 'error'
    app.config['FREEZER_DESTINATION_IGNORE'] = ['.git*']

    # TODO: check for uncommited changes in outdir; also, probably pull

    freezer = make_freezer(app)

    progressbar = click.progressbar(
        freezer.freeze_yield(),
        item_show_func=lambda p: p.url if p else 'Done!',
        show_pos=True,
    )
    with progressbar as urls:
        for url in urls:
            pass

    app.enable_checks()
    test_client = app.test_client()

    errors = test_client.get('/_check/internal-urls.json').json['errors']
    if errors:
        errors_str = yaml.safe_dump(errors)
        raise click.ClickException(f"Broken internal URLs:\n\n{errors_str}\n")

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


if __name__ == '__main__':
    cli()

import os.path
import click

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
@click.pass_obj
def serve(project, host, port):
    from werkzeug.serving import run_simple
    from .app import create_app
    # TODO: threads, reload, debug
    create_app(project, f"http://{host}:{port}").run(host, port)


@cli.command()
@click.argument(
    'outdir',
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    '-f', '--force/--no-force',
    help=
        "Overwrite any previously generated files. "
        "WARNING: All other files will be deleted.",
)
@click.pass_obj
def freeze(project, outdir, force):
    if os.path.exists(outdir) and not force:
        click.confirm(
            f"{click.style('WARNING', fg='red', bold=True)}: {outdir} exists. \n"
            "Previously generated files will be overwritten. \n"
            "All other files will be deleted.\n"
            "Proceed?"
            ,
            abort=True,
        )
        
    from .app import create_app
    # FIXME: project_url
    app = create_app(project, '')
    
    app.config['FREEZER_DESTINATION'] = outdir
    app.config['FREEZER_REDIRECT_POLICY'] = 'error'

    freezer = make_freezer(app)
    
    progressbar = click.progressbar(
        freezer.freeze_yield(),
        item_show_func=lambda p: p.url if p else 'Done!',
        show_pos=True,
    )
    with progressbar as urls:
        for url in urls:
            pass


if __name__ == '__main__':
    cli()    

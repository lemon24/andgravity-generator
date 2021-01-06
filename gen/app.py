import os.path

from flask import Flask, Blueprint, render_template, current_app, g, url_for, abort, Response
import jinja2
import markupsafe
import feedgen.feed

from .markdown import make_markdown
from .core import Thingie


def build_url(id):
    # check for existence
    # TODO: raise a nicer exception
    page = get_thingie().get_page(id)
    return url_for("main.page", id=id)

def get_thingie():
    if hasattr(g, 'thingie'):
        return g.thingie
    # TODO: get path from app config
    g.thingie = Thingie(os.path.join(current_app.project_root, 'content'))
    return g.thingie


main_bp = Blueprint('main', __name__)

@main_bp.app_template_filter('markdown')
def markdown_filter(md):
    return markupsafe.Markup(current_app.markdown(md))


@main_bp.route('/', defaults={'id': 'index'})
@main_bp.route('/<id>')  
def page(id):
    try:
        page = get_thingie().get_page(id)
    # TODO: be more precise in what we're catching
    except FileNotFoundError:
        return abort(404)
    # TODO: page should have a template attribute we're using
    template = current_app.jinja_env.select_template(
        [os.path.join('custom', id + '.html'), 'base.html']
    )
    return render_template(template, page=page)


feed_bp = Blueprint('feed', __name__)

def abs_page_url_for(id):
    return current_app.project_url + url_for('main.page', id=id)
    
def abs_feed_url_for(id):
    return current_app.project_url + url_for('feed.feed', id=id)


@feed_bp.route('/<id>')  
def feed(id):
    try:
        page = get_thingie().get_page(id)
    # TODO: be more precise in what we're catching
    except FileNotFoundError:
        return abort(404)
    
    fg = feedgen.feed.FeedGenerator()
    fg.id(abs_page_url_for(id))  # required
    
    feed_title = page.title
    if id != 'index':
        feed_title = get_thingie().get_page('index').title + ': ' + feed_title
    fg.title(feed_title)  # required

    fg.link(href=abs_page_url_for(id), rel='alternate')
    fg.link(href=abs_feed_url_for(id), rel='self' )
    # TODO: generator

    # sort ascending, because feedgen reverses the entries
    children = list(get_thingie().get_children(id, sort='updated'))

    if not children:
        feed_updated = '1970-01-01T00:00:00Z'
    else:
        feed_updated = children[-1].meta['updated']
    fg.updated(feed_updated)  # required

    for child in children:
        fe = fg.add_entry()
        fe.id(abs_page_url_for(child.id))  # required
        fe.title(child.title)  # required
        fe.link(href=abs_page_url_for(child.id))
        
        # TODO: published
        fe.updated(child.meta['updated'])  # required
        
        # TODO: summary feature
        fe.content(content=current_app.markdown(child.content), type='html')
        
    return Response(
        fg.atom_str(pretty=True), 
        # should be application/atom+xml, but we get warnings when freezing
        mimetype='application/xml', 
    )
        
        
def create_app(project_root, project_url):
    app = Flask(
        __name__, 
        template_folder=os.path.join(project_root, 'templates'),
        static_url_path='/_static',
        static_folder=os.path.join(project_root, 'static'),
    )
    app.project_root = project_root
    app.project_url = project_url
    app.jinja_env.undefined = jinja2.StrictUndefined
    app.add_template_global(get_thingie)
    app.markdown = make_markdown(build_url)
    app.register_blueprint(main_bp)
    app.register_blueprint(feed_bp, url_prefix='/_feed')
    return app


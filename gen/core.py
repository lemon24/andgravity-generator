import os.path
from dataclasses import dataclass
from functools import cached_property

import yaml


@dataclass
class Thingie:
    path: str

    # TODO: pluggable loader

    def get_page_paths(self):
        for entry in os.scandir(self.path):
            if not entry.is_file():
                continue
            name, ext = os.path.splitext(entry.name)
            if ext != '.md':
                continue
            yield name, entry.name

    def get_all_page_ids(self):
        for name, _ in self.get_page_paths():
            yield name

    def get_pages(
        self,
        *,
        hidden=False,
        discoverable=True,
        tags=None,
        invert=False,
        sort='id',
        reverse=False,
    ):
        filters = []
        if hidden is not None:
            filters.append(lambda p: p.hidden is bool(hidden))
        if discoverable is not None:
            filters.append(lambda p: p.discoverable is bool(discoverable))
        if tags is not None:
            filters.append(lambda p: any(tag in p.tags for tag in tags))
        if sort != 'id':
            filters.append(lambda p: sort in p.meta)

        keep = lambda p: invert is not all(f(p) for f in filters)

        rv = filter(keep, map(self.get_page, self.get_all_page_ids()))

        if sort == 'id' or invert:
            key = lambda p: p.id
        else:
            if sort not in ('published',):
                raise ValueError(f"unknown sort: {sort!r}")
            key = lambda p: p.meta[sort]

        rv = sorted(rv, key=key, reverse=reverse)

        return iter(rv)

    def get_page_ids(self, *, hidden=False, discoverable=True, tags=None, invert=False):
        # TODO: this is inefficient
        for page in self.get_pages(
            hidden=hidden, discoverable=discoverable, tags=tags, invert=invert
        ):
            yield page.id

    def page_exists(self, id):
        return os.path.exists(os.path.join(self.path, id) + '.md')

    def get_page_metadata(self, id):
        with open(os.path.join(self.path, id) + '.md') as f:
            lines = list(read_metadata(f))
        rv = yaml.safe_load(''.join(lines)) or {}
        if not isinstance(rv, dict):
            raise ValueError(
                f"bad metadata (expected dict, got {type(rv).__name__}): {id}"
            )
        return rv

    def get_page_content(self, id):
        with open(os.path.join(self.path, id) + '.md') as f:
            for line in read_metadata(f):
                pass
            return f.read()

    def get_page(self, id):
        if not self.page_exists(id):
            raise FileNotFoundError(os.path.join(self.path, id) + '.md')  # :(
        return Page(id, self)

    def get_children(
        self,
        id,
        *,
        sort='id',
        reverse=False,
        hidden=False,
        discoverable=True,
        tags=None,
        invert=False,
    ):
        if id != 'index':
            return

        children = self.get_pages(
            hidden=hidden,
            discoverable=discoverable,
            tags=tags,
            sort=sort,
            reverse=reverse,
            invert=invert,
        )
        for child in children:
            if child.id == 'index':
                continue
            yield child

    def get_project_url(self):
        return self.get_page('index').meta['project-url']


@dataclass
class Page:
    id: str
    thingie: Thingie

    # TODO: required attributes, schema

    @cached_property
    def meta(self):
        return self.thingie.get_page_metadata(self.id)

    @cached_property
    def content(self):
        return self.thingie.get_page_content(self.id)

    @property
    def title(self):
        return self.meta.get('title', self.id)

    @property
    def summary(self):
        return self.meta.get('summary')

    @property
    def tags(self):
        tags = self.meta.get('tags') or []
        error = ValueError(f"bad tags for {self.id}: {tags!r}")
        if not isinstance(tags, list):
            raise error
        for tag in tags:
            if not isinstance(tag, str):
                raise error
        return tags

    @property
    def tag_feeds(self):
        tag_feeds = self.meta.get('tag-feeds') or []
        error = ValueError(f"bad tags-feed for {self.id}: {tag_feeds!r}")
        if not isinstance(tag_feeds, list):
            raise error
        for tags in tag_feeds:
            if not isinstance(tags, list):
                raise error
            if not tags:
                raise error
            for tag in tags:
                if not isinstance(tag, str):
                    raise error
        return tag_feeds

    @property
    def has_feed(self):
        has_feed = self.meta.get('has-feed', False)
        if not isinstance(has_feed, bool):
            raise ValueError(f"bad has-feed for {self.id}: {has_feed!r}")
        return has_feed

    @property
    def series(self):
        return [tag for tag in self.tags if tag.startswith('series-')]

    @property
    def hidden(self) -> bool:
        return bool(self.meta.get('hidden', False))

    @property
    def discoverable(self) -> bool:
        return bool(self.meta.get('discoverable', True))


def read_metadata(file):
    initial_offset = file.tell()

    try:
        line = next(file)
    except StopIteration:
        return

    if not line.rstrip() == '---':
        file.seek(initial_offset)
        return

    for line in file:
        if line.rstrip() == '---':
            break
        yield line
    else:
        file.seek(initial_offset)
        raise ValueError("could not find end metadata marker")

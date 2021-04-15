import os.path
from dataclasses import dataclass


@dataclass
class Thingie:
    path: str

    # TODO: pluggable loader

    def get_pages(
        self, *, hidden=False, discoverable=True, tags=None, sort='id', reverse=False
    ):
        def generate():
            for entry in os.scandir(self.path):
                if not entry.is_file():
                    continue
                name, ext = os.path.splitext(entry.name)
                if ext != '.md':
                    continue

                page = self.get_page(name)
                meta = page.meta

                if hidden is not None:
                    # TODO: hidden pages will still be generated if someone links them explicitly
                    if bool(meta.get('hidden', False)) is not bool(hidden):
                        continue

                if discoverable is not None:
                    if bool(meta.get('discoverable', True)) is not bool(discoverable):
                        continue

                if tags is not None:
                    if not any(tag in page.tags for tag in tags):
                        continue

                yield page

        rv = generate()
        if sort == 'id':
            rv = sorted(rv, key=lambda p: p.id, reverse=reverse)
        elif sort == 'published':
            rv = (p for p in rv if 'published' in p.meta)
            rv = sorted(rv, key=lambda p: p.meta['published'], reverse=reverse)
        else:
            raise ValueError(f"unknown sort: {sort!r}")

        return iter(rv)

    def get_page_ids(self, *, hidden=False, discoverable=True, tags=None):
        # TODO: this is inefficient
        for page in self.get_pages(hidden=hidden, discoverable=discoverable, tags=tags):
            yield page.id

    def page_exists(self, id):
        return os.path.exists(os.path.join(self.path, id) + '.md')

    def get_page(self, id):
        with open(os.path.join(self.path, id) + '.md') as f:
            metadata = load_metadata(f) or {}
            content = f.read()
        return Page(id, content, metadata)

    def get_children(
        self,
        id,
        *,
        sort='id',
        reverse=False,
        hidden=False,
        discoverable=True,
        tags=None,
    ):
        if id != 'index':
            return

        children = self.get_pages(
            hidden=hidden,
            discoverable=discoverable,
            tags=tags,
            sort=sort,
            reverse=reverse,
        )
        for child in children:
            if child.id == 'index':
                continue
            yield child


@dataclass
class Page:
    id: str
    content: str
    meta: dict

    # TODO: eager loading
    # TODO: required attributes

    @property
    def title(self):
        return self.meta.get('title', self.id)

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


import yaml


def load_metadata(file):
    initial_offset = file.tell()

    try:
        line = next(file)
    except StopIteration:
        return None

    if not line.rstrip() == '---':
        file.seek(initial_offset)
        return None

    lines = [line]
    for line in file:
        lines.append(line)
        if line.rstrip() == '---':
            break

    try:
        return yaml.safe_load(''.join(lines[:-1]))
    except:
        file.seek(initial_offset)
        raise

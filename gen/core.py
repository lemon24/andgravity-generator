import os.path
from dataclasses import dataclass

@dataclass
class Thingie:
    path: str

    # TODO: pluggable loader

    def get_page_ids(self, hidden=False, discoverable=True):
        for entry in os.scandir(self.path):
            if not entry.is_file():
                continue
            name, ext = os.path.splitext(entry.name)
            if ext != '.md':
                continue
            
            # TODO: this is inefficient
            meta = self.get_page(name).meta

            if hidden is not None:
                # TODO: hidden pages will still be generated if someone links them explicitly
                if bool(meta.get('hidden', False)) is not bool(hidden):
                    continue

            if discoverable is not None:
                if bool(meta.get('discoverable', True)) is not bool(discoverable):
                    continue
            
            yield name

    def page_exists(self, id):
        return os.path.exists(os.path.join(self.path, id) + '.md')

    def get_page(self, id):
        with open(os.path.join(self.path, id) + '.md') as f:
            metadata = load_metadata(f) or {}
            content = f.read()
        return Page(id, content, metadata)
    
    def get_children(self, id, sort='id', reverse=False, hidden=False, discoverable=True):

        def generate():
            if id != 'index':
                return
            # TODO: order by something
            for child_id in self.get_page_ids(hidden=hidden, discoverable=discoverable):
                if child_id == 'index':
                    continue
                yield self.get_page(child_id)
            
        rv = generate()
        if sort == 'id':
            rv = sorted(rv, key=lambda p: p.id, reverse=reverse)
        elif sort == 'updated':
            rv = (p for p in rv if 'updated' in p.meta)
            rv = sorted(rv, key=lambda p: p.meta['updated'], reverse=reverse) 
        else:
            raise ValueError(f"unknown sort: {sort!r}")

        return iter(rv)
    

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




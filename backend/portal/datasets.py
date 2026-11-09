from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import PurePosixPath
import stat
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe


@contextmanager
def open_dataset_path(relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or '\x00' in relative:
        raise Http404
    descriptor = None
    try:
        descriptor = os.open(settings.PLATFORM_DATASET_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        for index, part in enumerate(path.parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if index < len(path.parts)-1:
                flags |= os.O_DIRECTORY
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    except OSError as exc:
        raise Http404('目录或文件不可访问。') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def dataset_content(relative, query=''):
    path = PurePosixPath(relative)
    query = query.strip()
    with open_dataset_path(relative) as descriptor:
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISREG(mode):
            stream = os.fdopen(os.dup(descriptor), 'rb')
            return FileResponse(stream, as_attachment=True, filename=path.name, content_type='application/octet-stream')
        if not stat.S_ISDIR(mode):
            raise Http404
        entries = []
        with os.scandir(descriptor) as directory:
            for entry in directory:
                if query and query.casefold() not in entry.name.casefold():
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                kind = 'directory' if stat.S_ISDIR(metadata.st_mode) else 'file' if stat.S_ISREG(metadata.st_mode) else 'link'
                child = path / entry.name
                entries.append({'name': entry.name, 'kind': kind, 'size': metadata.st_size,
                    'modified': datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
                    'path': str(child), 'container_path': '/datasets/'+str(child)})
        entries.sort(key=lambda item: (item['kind'] != 'directory', item['name'].casefold()))
    return entries


@login_required
@require_safe
def browse_datasets(request, management=False):
    if management and not request.user.is_staff:
        raise PermissionDenied
    path = PurePosixPath(request.GET.get('path', ''))
    relative = str(path) if str(path) != '.' else ''
    query = request.GET.get('q', '').strip()
    endpoint = reverse('manage_datasets' if management else 'datasets')
    def url(path, **kwargs):
        return endpoint + '?' + urlencode({'path': str(path) if str(path) != '.' else '', **kwargs})
    entries = dataset_content(relative, query)
    if isinstance(entries, FileResponse):
        return entries
    for entry in entries:
        entry['url'] = url(entry['path']) if entry['kind'] != 'link' else None
    crumbs = [{'name': '公用目录', 'url': endpoint}]
    for index, part in enumerate(path.parts):
        crumbs.append({'name': part, 'url': url(PurePosixPath(*path.parts[:index+1]))})
    page = Paginator(entries, 100).get_page(request.GET.get('page'))
    return render(request, 'portal/datasets.html', {'base_template': 'portal/manage_base.html' if management else 'portal/base.html',
        'section': 'datasets', 'section_title': '公用目录', 'management': management, 'page': page,
        'query': query, 'relative': relative, 'breadcrumbs': crumbs, 'parent_url': url(path.parent) if path.parts else None})

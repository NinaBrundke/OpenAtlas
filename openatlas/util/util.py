from __future__ import annotations  # Needed for Python 4.0 type annotations

import glob
import os
import re
import smtplib
from collections import OrderedDict
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from functools import wraps
from html.parser import HTMLParser
from os.path import basename
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple, Union

import numpy
from flask import abort, flash, g, request, session, url_for
from flask_babel import format_number, lazy_gettext as _
from flask_login import current_user
from flask_wtf import FlaskForm
from markupsafe import Markup
from numpy import math
from werkzeug.utils import redirect

import openatlas
from openatlas import app
from openatlas.api.error import APIError
from openatlas.models.date import Date
from openatlas.models.model import CidocClass, CidocProperty

if TYPE_CHECKING:  # pragma: no cover - Type checking is disabled in tests
    from openatlas.models.entity import Entity
    from openatlas.models.imports import Project
    from openatlas.models.user import User
    from openatlas.models.link import Link


def convert_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0B"  # pragma: no cover
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    return "%s %s" % (int(size_bytes / math.pow(1024, i)), size_name[i])


def get_file_path(entity: Union[int, 'Entity']) -> Optional[str]:
    entity_id = entity if isinstance(entity, int) else entity.id
    path = glob.glob(os.path.join(app.config['UPLOAD_FOLDER_PATH'], str(entity_id) + '.*'))
    return path[0] if path else None


def print_file_size(entity: 'Entity') -> str:
    path = get_file_path(entity.id)
    return convert_size(os.path.getsize(path)) if path else 'N/A'


def display_tooltip(text: str) -> str:
    if not text:
        return ''
    return '<span><i class="fas fa-info-circle tooltipicon" title="{title}"></i></span>'.format(
        title=text.replace('"', "'"))


def get_file_extension(entity: Union[int, 'Entity']) -> str:
    path = get_file_path(entity if isinstance(entity, int) else entity.id)
    return os.path.splitext(path)[1] if path else 'N/A'


def send_mail(subject: str,
              text: str,
              recipients: Union[str, List[str]],
              log_body: bool = True) -> bool:  # pragma: no cover
    """ Send one mail to every recipient, set log_body to False for sensitive data e.g. passwords"""
    recipients = recipients if isinstance(recipients, list) else [recipients]
    settings = session['settings']
    if not settings['mail'] or len(recipients) < 1:
        return False
    mail_user = settings['mail_transport_username']
    from_ = settings['mail_from_name'] + ' <' + settings['mail_from_email'] + '>'
    server = smtplib.SMTP(settings['mail_transport_host'], settings['mail_transport_port'])
    server.ehlo()
    server.starttls()
    try:
        if settings['mail_transport_username']:
            server.login(mail_user, app.config['MAIL_PASSWORD'])
        for recipient in recipients:
            msg = MIMEText(text, _charset='utf-8')
            msg['From'] = from_
            msg['To'] = recipient.strip()
            msg['Subject'] = Header(subject.encode('utf-8'), 'utf-8')
            server.sendmail(settings['mail_from_email'], recipient, msg.as_string())
        log_text = 'Mail from ' + from_ + ' to ' + ', '.join(recipients) + ' Subject: ' + subject
        log_text += ' Content: ' + text if log_body else ''
        openatlas.logger.log('info', 'mail', 'Mail send from ' + from_, log_text)
    except smtplib.SMTPAuthenticationError as e:
        openatlas.logger.log('error', 'mail', 'Error mail login for ' + mail_user, e)
        flash(_('error mail login'), 'error')
        return False
    except Exception as e:
        openatlas.logger.log('error', 'mail', 'Error send mail for ' + mail_user, e)
        flash(_('error mail send'), 'error')
        return False
    return True


class MLStripper(HTMLParser):

    def error(self: MLStripper, message: str) -> None:  # pragma: no cover
        pass

    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.fed: List[str] = []

    def handle_data(self, d: Any) -> None:
        self.fed.append(d)

    def get_data(self) -> str:
        return ''.join(self.fed)


def sanitize(string: Optional[str], mode: Optional[str] = None) -> str:
    if not string:
        return ''
    if mode == 'node':  # Only keep letters, numbers and spaces
        return re.sub(r'([^\s\w]|_)+', '', string).strip()
    if mode == 'text':  # Remove HTML tags, keep linebreaks
        s = MLStripper()
        s.feed(string)
        return s.get_data().strip()
    return re.sub('[^A-Za-z0-9]+', '', string)  # Only keep ASCII letters and numbers


def get_file_stats(path: str = app.config['UPLOAD_FOLDER_PATH']) -> Dict[Union[int, str], Any]:
    """ Build a dict with file ids and stats from files in given directory.
        It's much faster to do this in one call for every file."""
    file_stats: Dict[Union[int, str], Any] = {}
    with os.scandir(path) as it:
        for file in it:
            split_name = os.path.splitext(file.name)
            if len(split_name) > 1 and split_name[0].isdigit():
                file_stats[int(split_name[0])] = {'ext': split_name[1],
                                                  'size': file.stat().st_size,
                                                  'date': file.stat().st_ctime}
    return file_stats


def display_remove_link(url: str, name: str) -> str:
    """ Build a link to remove a link with a JavaScript confirmation dialog"""
    name = name.replace('\'', '')
    confirm = 'onclick="return confirm(\'' + _('Remove %(name)s?', name=name) + '\')"'
    return '<a ' + confirm + ' href="' + url + '">' + uc_first(_('remove')) + '</a>'


def get_disk_space_info() -> Optional[Dict[str, Any]]:
    if os.name != "posix":  # pragma: no cover - e.g. Windows has no statvfs
        return None
    statvfs = os.statvfs(app.config['UPLOAD_FOLDER_PATH'])
    disk_space = statvfs.f_frsize * statvfs.f_blocks
    free_space = statvfs.f_frsize * statvfs.f_bavail  # Available space without reserved blocks
    return {'total': convert_size(statvfs.f_frsize * statvfs.f_blocks),
            'free': convert_size(statvfs.f_frsize * statvfs.f_bavail),
            'percent': 100 - math.ceil(free_space / (disk_space / 100))}


def add_type_data(entity: 'Entity',
                  data: Dict[str, Union[str, List[str]]],
                  location: Optional['Entity'] = None) -> Dict[str, Union[str, List[str]]]:
    if location:
        entity.nodes.update(location.nodes)  # Add location types
    type_data: OrderedDict[str, Any] = OrderedDict()
    for node, node_value in entity.nodes.items():
        root = g.nodes[node.root[-1]]
        name = 'type' if root.name in app.config['BASE_TYPES'] else root.name
        if root.name not in type_data:
            type_data[name] = []
        text = ''
        if root.value_type:  # Text for value types
            text = ': {value} <span style="font-style:italic;">{description}</span>'.format(
                value=format_number(node_value), description=node.description)
        type_data[name].append('<span title="{path}">{link}</span>{text}'.format(
            link=link(node),
            path=' > '.join([g.nodes[id_].name for id_ in node.root]),
            text=text))

    # Sort types by name
    type_data = OrderedDict(sorted(type_data.items(), key=lambda t: t[0]))
    for root_type in type_data:
        type_data[root_type].sort()

    # Move the standard type to the top
    if 'type' in type_data:
        type_data.move_to_end('type', last=False)
    for root_name, nodes in type_data.items():
        data[root_name] = nodes
    return data


def add_system_data(entity: 'Entity',
                    data: Dict[str, Union[str, List[str]]]) -> Dict[str, Union[str, List[str]]]:
    # Additional info for advanced layout
    if hasattr(current_user, 'settings') and current_user.settings['layout'] == 'advanced':
        data[_('class')] = link(entity.class_)
        info = openatlas.logger.get_log_for_advanced_view(entity.id)
        data[_('created')] = format_date(entity.created) + ' ' + link(info['creator'])
        if info['modified']:
            html = format_date(info['modified']) + ' ' + link(info['modifier'])
            data[_('modified')] = html
        if info['import_project']:
            data[_('imported from')] = link(info['import_project'])
        if info['import_user']:
            data[_('imported by')] = link(info['import_user'])
        if info['import_origin_id']:
            data['origin ID'] = info['import_origin_id']
        data_api = '<a href="{url}" target="_blank">GeoJSON</a>'.format(
            url=url_for('api_entity', id_=entity.id))
        data_api += '''
            <a class="btn btn-outline-primary btn-sm" href="{url}" target="_blank" title="Download">
                <i class="fas fa-download"></i> {label}
            </a>'''.format(url=url_for('api_download_entity', id_=entity.id),
                           label=uc_first('download'))
        data['API'] = data_api
    return data


def get_entity_data(entity: 'Entity',
                    location: Optional['Entity'] = None) -> Dict[str, Union[str, List[str]]]:
    """
    Return related entity information for a table for view.
    The location parameter is for places which have a location attached.
    """
    data: Dict[str, Union[str, List[str]]] = {_('alias'): list(entity.aliases.values())}

    # Dates
    from_link = ''
    to_link = ''
    if entity.class_.code == 'E9':  # Add places to dates if it's a move
        place_from = entity.get_linked_entity('P27')
        if place_from:
            from_link = link(place_from.get_linked_entity_safe('P53', True)) + ' '
        place_to = entity.get_linked_entity('P26')
        if place_to:
            to_link = link(place_to.get_linked_entity_safe('P53', True)) + ' '
    data[_('begin')] = (from_link if from_link else '') + format_entry_begin(entity)
    data[_('end')] = (to_link if to_link else '') + format_entry_end(entity)

    # Types
    add_type_data(entity, data, location=location)

    # Info for files
    if entity.system_type == 'file':
        data[_('size')] = print_file_size(entity)
        data[_('extension')] = get_file_extension(entity)

    # Info for source
    if entity.system_type == 'source content':
        data[_('information carrier')] = [link(recipient) for recipient in
                                          entity.get_linked_entities(['P128'], inverse=True)]

    # Info for events
    if entity.class_.code in app.config['CLASS_CODES']['event']:
        super_event = entity.get_linked_entity('P117')
        if super_event:
            data[_('sub event of')] = link(super_event)
        if not entity.class_.code == 'E9':
            place = entity.get_linked_entity('P7')
            if place:
                data[_('location')] = link(place.get_linked_entity_safe('P53', True))

        # Info for acquisitions
        if entity.class_.code == 'E8':
            data[_('recipient')] = [link(recipient) for recipient in
                                    entity.get_linked_entities(['P22'])]
            data[_('donor')] = [link(donor) for donor in entity.get_linked_entities(['P23'])]
            data[_('given place')] = [link(place) for place in entity.get_linked_entities(['P24'])]

        # Info for moves
        if entity.class_.code == 'E9':
            person_data = []
            object_data = []
            for linked_entity in entity.get_linked_entities(['P25']):
                if linked_entity.class_.code == 'E21':
                    person_data.append(linked_entity)
                elif linked_entity.class_.code == 'E84':
                    object_data.append(linked_entity)
            data[_('person')] = [link(object_) for object_ in person_data]
            data[_('object')] = [link(object_) for object_ in object_data]
    return add_system_data(entity, data)


def add_dates_to_form(form: Any, for_person: bool = False) -> str:
    errors = {}
    valid_dates = True
    for field_name in ['begin_year_from', 'begin_month_from', 'begin_day_from',
                       'begin_year_to', 'begin_month_to', 'begin_day_to',
                       'end_year_from', 'end_month_from', 'end_day_from',
                       'end_year_to', 'end_month_to', 'end_day_to']:
        errors[field_name] = ''
        if getattr(form, field_name).errors:
            valid_dates = False
            errors[field_name] = '<label class="error">'
            for error in getattr(form, field_name).errors:
                errors[field_name] += uc_first(error)
            errors[field_name] += ' </label>'
    style = '' if valid_dates else ' style="display:table-row" '
    switch_label = _('hide') if form.begin_year_from.data or form.end_year_from.data else _('show')
    html = """
        <div class="table-row">
            <div>
                <label>{date}</label> {tooltip}
            </div>
            <div class="table-cell date-switcher">
                <span id="date-switcher" class="{button_class}">{show}</span>
            </div>
        </div>""".format(date=uc_first(_('date')),
                         button_class=app.config['CSS']['button']['secondary'],
                         tooltip=display_tooltip(_('tooltip date')),
                         show=uc_first(switch_label))
    html += '<div class="table-row date-switch" ' + style + '>'
    html += '<div>' + uc_first(_('birth') if for_person else _('begin')) + '</div>'
    html += '<div class="table-cell">'
    html += str(form.begin_year_from(class_='year')) + ' ' + errors['begin_year_from'] + ' '
    html += str(form.begin_month_from(class_='month')) + ' ' + errors['begin_month_from'] + ' '
    html += str(form.begin_day_from(class_='day')) + ' ' + errors['begin_day_from'] + ' '
    html += str(form.begin_comment)
    html += '</div></div>'
    html += '<div class="table-row date-switch" ' + style + '>'
    html += '<div></div><div class="table-cell">'
    html += str(form.begin_year_to(class_='year')) + ' ' + errors['begin_year_to'] + ' '
    html += str(form.begin_month_to(class_='month')) + ' ' + errors['begin_month_to'] + ' '
    html += str(form.begin_day_to(class_='day')) + ' ' + errors['begin_day_to'] + ' '
    html += '</div></div>'
    html += '<div class="table-row date-switch" ' + style + '>'
    html += '<div>' + uc_first(_('death') if for_person else _('end')) + '</div>'
    html += '<div class="table-cell">'
    html += str(form.end_year_from(class_='year')) + ' ' + errors['end_year_from'] + ' '
    html += str(form.end_month_from(class_='month')) + ' ' + errors['end_month_from'] + ' '
    html += str(form.end_day_from(class_='day')) + ' ' + errors['end_day_from'] + ' '
    html += str(form.end_comment)
    html += '</div></div>'
    html += '<div class="table-row date-switch"' + style + '>'
    html += '<div></div><div class="table-cell">'
    html += str(form.end_year_to(class_='year')) + ' ' + errors['end_year_to'] + ' '
    html += str(form.end_month_to(class_='month')) + ' ' + errors['end_month_to'] + ' '
    html += str(form.end_day_to(class_='day')) + ' ' + errors['end_day_to'] + ' '
    html += '</div></div>'
    return html


def required_group(group: str):  # type: ignore
    def wrapper(f):  # type: ignore
        @wraps(f)
        def wrapped(*args, **kwargs):  # type: ignore
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.path))
            if not is_authorized(group):
                abort(403)
            return f(*args, **kwargs)

        return wrapped

    return wrapper


def api_access():  # type: ignore
    def wrapper(f):  # type: ignore
        @wraps(f)
        def wrapped(*args, **kwargs):  # type: ignore
            if not current_user.is_authenticated and not session['settings']['api_public']:
                raise APIError('Syntax is incorrect!', status_code=403,
                               payload="403")  # pragma: nocover
            return f(*args, **kwargs)

        return wrapped

    return wrapper


def bookmark_toggle(entity_id: int, for_table: bool = False) -> str:
    label = uc_first(_('bookmark remove') if entity_id in current_user.bookmarks else _('bookmark'))
    if for_table:
        return """<a href='#' id="bookmark{id}" onclick="ajaxBookmark('{id}');">{label}
            </a>""".format(id=entity_id, label=label)
    return button(label,
                  id_='bookmark' + str(entity_id),
                  onclick="ajaxBookmark('" + str(entity_id) + "');")


def button(label: str,
           url: Optional[str] = '#',
           css: Optional[str] = 'primary',
           id_: Optional[str] = None,
           onclick: Optional[str] = '') -> str:
    label = uc_first(label)
    if url and '/insert' in url and label != uc_first(_('link')):
        label = '+ ' + label
    html = '<a class="{class_}" href="{url}" {id} {onclick}>{label}</a>'.format(
        class_=app.config['CSS']['button'][css],
        url=url,
        label=label,
        id='id="' + id_ + '"' if id_ else '',
        onclick='onclick="{onclick}"'.format(onclick=onclick) if onclick else '')
    return Markup(html)


def is_authorized(group: str) -> bool:
    if not current_user.is_authenticated or not hasattr(current_user, 'group'):
        return False  # pragma: no cover - needed because AnonymousUserMixin has no group

    if current_user.group == 'admin' or (
            current_user.group == 'manager' and group in
            ['manager', 'editor', 'contributor', 'readonly']) or (
            current_user.group == 'editor' and group in ['editor', 'contributor', 'readonly']) or (
            current_user.group == 'contributor' and group in ['contributor', 'readonly']) or (
            current_user.group == 'readonly' and group == 'readonly'):
        return True
    return False


def uc_first(string: str) -> str:
    return str(string)[0].upper() + str(string)[1:] if string else ''


def format_date(value: Union[datetime, numpy.datetime64]) -> str:
    if not value:
        return ''
    if isinstance(value, numpy.datetime64):
        date_ = Date.datetime64_to_timestamp(value)
        return date_ if date_ else ''
    return value.date().isoformat()


def format_datetime(value: Any) -> str:
    return value.replace(microsecond=0).isoformat() if value else ''


def get_profile_image_table_link(file: 'Entity',
                                 entity: 'Entity',
                                 extension: str,
                                 profile_image_id: Optional[int] = None) -> str:
    if file.id == profile_image_id:
        return link(_('unset'), url_for('file_remove_profile_image', entity_id=entity.id))
    elif extension in app.config['DISPLAY_FILE_EXTENSIONS']:
        return link(_('set'), url_for('set_profile_image', id_=file.id, origin_id=entity.id))
    return ''  # pragma: no cover - only happens for non image files


def link(object_: Union[str, 'Entity', CidocClass, CidocProperty, 'Project', 'User'],
         url: Optional[str] = None,
         class_: Optional[str] = None,
         uc_first_: Optional[bool] = True) -> str:

    if type(object_) is str:  # An link with given label and destination URL
        return '<a href="{url}" {class_}>{label}</a>'.format(
            url=url,
            label=uc_first(object_) if uc_first_ else object_,
            class_='class="' + class_ + '"' if class_ else '')

    # Builds an HTML link to a detail view of an object
    from openatlas.models.entity import Entity
    from openatlas.models.imports import Project
    from openatlas.models.user import User
    if type(object_) is Project:
        return link(object_.name, url_for('import_project_view', id_=object_.id))
    if isinstance(object_, User):
        return link(object_.username,
                    url_for('user_view', id_=object_.id),
                    class_='' if object_.active else 'inactive',
                    uc_first_=False)
    if isinstance(object_, CidocClass):
        return link(object_.code, url_for('class_view', code=object_.code), uc_first_=False)
    if isinstance(object_, CidocProperty):
        return link(object_.code, url_for('property_view', code=object_.code), uc_first_=False)
    if isinstance(object_, Entity):
        return link(object_.name, url_for('entity_view', id_=object_.id), uc_first_=False)
    return ''


def truncate(string: Optional[str] = '', length: int = 40, span: bool = True) -> str:
    """
    Returns a truncates string with '..' at the end if it was longer than length
    Also adds a span title (for mouse over) with the original string if parameter "span" is True
    """
    if string is None:
        return ''  # pragma: no cover
    if len(string) < length + 1:
        return string
    if not span:
        return string[:length] + '..'
    return '<span title="' + string.replace('"', '') + '">' + string[:length] \
           + '..' + '</span>'  # pragma: no cover


def get_base_table_data(entity: 'Entity',
                        file_stats: Optional[Dict[Union[int, str], Any]] = None) -> List[str]:
    """ Returns standard table data for an entity"""
    if len(entity.aliases) > 0:
        data: List[str] = ['<p>' + link(entity) + '</p>']
    else:
        data = [link(entity)]
    # Aliases
    for i, (id_, alias) in enumerate(entity.aliases.items()):
        if i == len(entity.aliases) - 1:
            data[0] = ''.join([data[0]] + [alias])
        else:
            data[0] = ''.join([data[0]] + ['<p>' + alias + '</p>'])
    if entity.view_name in ['event', 'actor']:
        data.append(g.classes[entity.class_.code].name)
    if entity.view_name in ['reference'] and entity.system_type != 'file':
        data.append(uc_first(_(entity.system_type)))
    if entity.view_name in ['event', 'place', 'source', 'reference', 'file', 'object']:
        data.append(entity.print_base_type())
    if entity.system_type == 'file':
        if file_stats:
            data.append(convert_size(
                file_stats[entity.id]['size']) if entity.id in file_stats else 'N/A')
            data.append(
                file_stats[entity.id]['ext'] if entity.id in file_stats else 'N/A')
        else:
            data.append(print_file_size(entity))
            data.append(get_file_extension(entity))
    if entity.view_name in ['event', 'actor', 'place']:
        data.append(entity.first if entity.first else '')
        data.append(entity.last if entity.last else '')
    data.append(entity.description)
    return data


def was_modified(form: FlaskForm, entity: 'Entity') -> bool:  # pragma: no cover
    """ Checks if an entity was modified after an update form was opened."""
    if not entity.modified or not form.opened.data:
        return False
    if entity.modified < datetime.fromtimestamp(float(form.opened.data)):
        return False
    openatlas.logger.log('info', 'multi user', 'Multi user overwrite prevented.')
    return True


def format_entry_begin(entry: Union['Entity', 'Link'], object_: Optional['Entity'] = None) -> str:
    html = link(object_) if object_ else ''
    if entry.begin_from:
        html += ', ' if html else ''
        if entry.begin_to:
            html += _('between %(begin)s and %(end)s',
                      begin=format_date(entry.begin_from), end=format_date(entry.begin_to))
        else:
            html += format_date(entry.begin_from)
    html += (' (' + entry.begin_comment + ')') if entry.begin_comment else ''
    return html


def format_entry_end(entry: 'Entity', object_: Optional['Entity'] = None) -> str:
    html = link(object_) if object_ else ''
    if entry.end_from:
        html += ', ' if html else ''
        if entry.end_to:
            html += _('between %(begin)s and %(end)s',
                      begin=format_date(entry.end_from), end=format_date(entry.end_to))
        else:
            html += format_date(entry.end_from)
    html += (' (' + entry.end_comment + ')') if entry.end_comment else ''
    return html


def get_appearance(event_links: List['Link']) -> Tuple[str, str]:
    # Get first/last appearance from events for actors without begin/end
    first_year = None
    last_year = None
    first_string = ''
    last_string = ''
    for link_ in event_links:
        event = link_.domain
        actor = link_.range
        event_link = link(_('event'), url_for('entity_view', id_=event.id))
        if not actor.first:
            if link_.first and (not first_year or int(link_.first) < int(first_year)):
                first_year = link_.first
                first_string = format_entry_begin(link_) + ' ' + _('at an') + ' ' + event_link
                first_string += (' ' + _('in') + ' ' + link(link_.object_)) if link_.object_ else ''
            elif event.first and (not first_year or int(event.first) < int(first_year)):
                first_year = event.first
                first_string = format_entry_begin(event) + ' ' + _('at an') + ' ' + event_link
                first_string += (' ' + _('in') + ' ' + link(link_.object_)) if link_.object_ else ''
        if not actor.last:
            if link_.last and (not last_year or int(link_.last) > int(last_year)):
                last_year = link_.last
                last_string = format_entry_end(event) + ' ' + _('at an') + ' ' + event_link
                last_string += (' ' + _('in') + ' ' + link(link_.object_)) if link_.object_ else ''
            elif event.last and (not last_year or int(event.last) > int(last_year)):
                last_year = event.last
                last_string = format_entry_end(event) + ' ' + _('at an') + ' ' + event_link
                last_string += (' ' + _('in') + ' ' + link(link_.object_)) if link_.object_ else ''
    return first_string, last_string


def get_backup_file_data() -> Dict[str, Any]:
    path = app.config['EXPORT_FOLDER_PATH'].joinpath('sql')
    latest_file = None
    latest_file_date = None
    for file in [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]:
        if basename(file) == '.gitignore':
            continue
        file_date = datetime.utcfromtimestamp(os.path.getmtime(path.joinpath(file)))
        if not latest_file_date or file_date > latest_file_date:
            latest_file = file
            latest_file_date = file_date
    file_data: Dict[str, Any] = {'backup_too_old': True}
    if latest_file and latest_file_date:
        yesterday = datetime.today() - timedelta(days=1)
        file_data['file'] = latest_file
        file_data['backup_too_old'] = True if yesterday > latest_file_date else False
        file_data['size'] = convert_size(os.path.getsize(path.joinpath(latest_file)))
        file_data['date'] = format_date(latest_file_date)
    return file_data


def is_float(value: Union[int, float]) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False

# Copyright 2017 by Alexander Watzinger and others. Please see README.md for licensing information
from flask import flash, render_template, url_for
from flask_babel import lazy_gettext as _
from flask_wtf import Form
from werkzeug.utils import redirect
from wtforms import HiddenField, StringField, SubmitField, TextAreaField, IntegerField
from wtforms.validators import InputRequired, NumberRange, Optional

import openatlas
from openatlas import app
from openatlas.models.entity import EntityMapper
from openatlas.util.util import link, required_group, truncate_string, uc_first


class EventForm(Form):
    name = StringField(_('name'), validators=[InputRequired()])
    date_begin_year = IntegerField(
        uc_first(_('begin')),
        render_kw={'placeholder': _('yyyy')},
        validators=[Optional(), NumberRange(min=-4713)]
    )
    date_begin_month = IntegerField(
        render_kw={'placeholder': _('mm')},
        validators=[Optional(), NumberRange(min=1,max=12)]
    )
    date_begin_day = IntegerField(
        render_kw={'placeholder': _('dd')},
        validators=[Optional(), NumberRange(min=1, max=31)]
    )
    date_begin_year2 = IntegerField(
        render_kw={'placeholder': _('yyyy')},
        validators=[Optional(), NumberRange(min=-4713)]
    )
    date_begin_month2 = IntegerField(
        render_kw={'placeholder': _('mm')},
        validators=[Optional(), NumberRange(min=1, max=12)]
    )
    date_begin_day2 = IntegerField(
        render_kw={'placeholder': _('dd')},
        validators=[Optional(), NumberRange(min=1, max=31)]
    )
    date_begin_info = StringField(render_kw={'placeholder': _('comment')},)
    date_end_year = IntegerField(
        uc_first(_('end')),
        render_kw={'placeholder': _('yyyy')},
        validators=[Optional(), NumberRange(min=-4713)]
    )
    date_end_month = IntegerField(
        render_kw={'placeholder': _('mm')},
        validators=[Optional(), NumberRange(min=1, max=12)]
    )
    date_end_day = IntegerField(
        render_kw={'placeholder': _('dd')},
        validators=[Optional(), NumberRange(min=1, max=31)]
    )
    date_end_year2 = IntegerField(
        render_kw={'placeholder': _('yyyy')},
        validators=[Optional(), NumberRange(min=-4713)]
    )
    date_end_month2 = IntegerField(
        render_kw={'placeholder': _('mm')},
        validators=[Optional(), NumberRange(min=1, max=12)]
    )
    date_end_day2 = IntegerField(
        render_kw={'placeholder': _('dd')},
        validators=[Optional(), NumberRange(min=1, max=31)]
    )
    date_end_info = StringField(render_kw={'placeholder': _('comment')})
    description = TextAreaField(uc_first(_('description')))
    save = SubmitField(_('save'))
    insert_and_continue = SubmitField(_('insert and continue'))
    continue_ = HiddenField()

    def populate_dates(self, entity):
        for code, types in entity.dates.items():
            if code in ['OA1', 'OA3', 'OA5']:
                for type_, date in types.items():
                    if type_ in ['Exact date value', 'From date value']:
                        self.date_begin_year.data = date['timestamp'].year
                        self.date_begin_month.data = date['timestamp'].month
                        self.date_begin_day.data = date['timestamp'].day
                        self.date_begin_info.data = date['info']
                    else:
                        self.date_begin_year2.data = date['timestamp'].year
                        self.date_begin_month2.data = date['timestamp'].month
                        self.date_begin_day2.data = date['timestamp'].day
            else:
                for type_, date in types.items():
                    if type_ in ['Exact date value', 'From date value']:
                        self.date_end_year.data = date['timestamp'].year
                        self.date_end_month.data = date['timestamp'].month
                        self.date_end_day.data = date['timestamp'].day
                        self.date_end_info.data = date['info']
                    else:
                        self.date_end_year2.data = date['timestamp'].year
                        self.date_end_month2.data = date['timestamp'].month
                        self.date_end_day2.data = date['timestamp'].day


@app.route('/event')
@required_group('readonly')
def event_index():
    tables = {'event': {
        'name': 'event',
        'header': [_('name'), _('class'), _('first'), _('last'), _('info')],
        'data': []}}
    for event in EntityMapper.get_by_codes(['E7', 'E8', 'E12', 'E6']):
        tables['event']['data'].append([
            link(event),
            openatlas.classes[event.class_.id].name,
            format(event.first),
            format(event.last),
            truncate_string(event.description)
        ])
    return render_template('event/index.html', tables=tables)


@app.route('/event/insert/<code>', methods=['POST', 'GET'])
@required_group('editor')
def event_insert(code):
    nodes = {}
    for node_id in openatlas.node.NodeMapper.get_hierarchy_by_name('Date value type').subs:
        nodes[openatlas.nodes[node_id].name] = node_id
    form = EventForm()
    if form.validate_on_submit() and form.name.data != openatlas.app.config['EVENT_ROOT_NAME']:
        openatlas.get_cursor().execute('BEGIN')
        event = EntityMapper.insert(code, form.name.data, form.description.data)
        event.save_dates(form)
        openatlas.get_cursor().execute('COMMIT')
        flash(_('entity created'), 'info')
        if form.continue_.data == 'yes':
            return redirect(url_for('event_insert', code=code))
        return redirect(url_for('event_view', event_id=event.id))
    return render_template('event/insert.html', form=form, code=code, nodes=nodes)


@app.route('/event/delete/<int:event_id>')
@required_group('editor')
def event_delete(event_id):
    if EntityMapper.get_by_id(event_id).name == openatlas.app.config['EVENT_ROOT_NAME']:
        flash(_('error forbidden'), 'error')
        return redirect(url_for('event_index'))
    openatlas.get_cursor().execute('BEGIN')
    EntityMapper.delete(event_id)
    openatlas.get_cursor().execute('COMMIT')
    flash(_('entity deleted'), 'info')
    return redirect(url_for('event_index'))


@app.route('/event/update/<int:event_id>', methods=['POST', 'GET'])
@required_group('editor')
def event_update(event_id):
    event = EntityMapper.get_by_id(event_id)
    event.set_dates()
    form = EventForm()
    del form.insert_and_continue
    if event.name == openatlas.app.config['EVENT_ROOT_NAME']:
        flash(_('error forbidden'), 'error')
        return redirect(url_for('event_index'))
    if form.validate_on_submit() and form.name.data != openatlas.app.config['EVENT_ROOT_NAME']:
        event.name = form.name.data
        event.description = form.description.data
        openatlas.get_cursor().execute('BEGIN')
        event.update()
        event.delete_dates()
        event.save_dates(form)
        openatlas.get_cursor().execute('COMMIT')
        flash(_('info update'), 'info')
        return redirect(url_for('event_view', event_id=event.id))
    form.name.data = event.name
    form.description.data = event.description
    form.populate_dates(event)
    return render_template('event/update.html', form=form, event=event)


@app.route('/event/view/<int:event_id>')
@required_group('readonly')
def event_view(event_id):
    event = EntityMapper.get_by_id(event_id)
    event.set_dates()
    data = {'info': [(_('name'), event.name)]}
    return render_template('event/view.html', event=event, data=data)

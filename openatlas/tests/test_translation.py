# Copyright 2017 by Alexander Watzinger and others. Please see README.md for licensing information
from flask import url_for

from openatlas import app, EntityMapper
from openatlas.test_base import TestBaseCase


class TranslationTest(TestBaseCase):

    def test_source(self):
        self.login()
        with app.app_context():
            source_id = EntityMapper.insert('E33', 'Necronomicon', 'source content').id
            rv = self.app.get(url_for('translation_insert', source_id=source_id))
            assert b'+ Translation' in rv.data
            rv = self.app.post(
                url_for('translation_insert', source_id=source_id),
                data={'name': 'Test translation'})
            translation_id = rv.location.split('/')[-1]
            self.app.get(url_for('translation_update', id_=translation_id, source_id=source_id))
            rv = self.app.post(
                url_for('translation_update', id_=translation_id, source_id=source_id),
                data={'name': 'Translation updated'},
                follow_redirects=True)
            assert b'Translation updated' in rv.data
            rv = self.app.get(
                url_for('translation_delete', id_=translation_id, source_id=source_id),
                follow_redirects=True)
            assert b'The entry has been deleted.' in rv.data
            self.app.post(
                url_for('translation_insert', source_id=source_id),
                data={'name': 'Translation continued', 'continue_': 'yes'})
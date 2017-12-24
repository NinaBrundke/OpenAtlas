# Copyright 2017 by Alexander Watzinger and others. Please see README.md for licensing information
import bcrypt
from bcrypt import hashpw
import datetime

import openatlas
from openatlas import app
from flask import abort, render_template, request, flash, url_for, session
from flask_babel import lazy_gettext as _
from flask_login import current_user, LoginManager, login_required, login_user, logout_user
from flask_wtf import Form
from werkzeug.utils import redirect
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import InputRequired, Email
from openatlas.models.user import UserMapper
from openatlas.util.util import send_mail, uc_first

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return UserMapper.get_by_id(user_id, True)


class LoginForm(Form):
    username = StringField(_('username'), validators=[InputRequired()])
    password = PasswordField(_('password'), validators=[InputRequired()])
    show_passwords = BooleanField(_('show password'))
    save = SubmitField(_('login'))


class PasswordResetForm(Form):
    email = StringField(_('email'), validators=[InputRequired(), Email()])
    save = SubmitField(_('submit'))


@app.route('/login', methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect('/')
    form = LoginForm()
    if form.validate_on_submit():
        user = UserMapper.get_by_username(request.form['username'])
        if user:
            if user.login_attempts_exceeded():
                openatlas.logger.log('notice', 'auth', 'Login attempts exceeded: ' + user.username)
                flash(_('error login attempts exceeded'), 'error')
                return render_template('login/index.html', form=form)
            hash_ = hashpw(request.form['password'].encode('utf-8'), user.password.encode('utf-8'))
            if hash_ == user.password.encode('utf-8'):
                if user.active:
                    login_user(user)
                    session['login_previous_success'] = user.login_last_success
                    session['login_previous_failures'] = user.login_failed_count
                    user.login_last_success = datetime.datetime.now()
                    user.login_failed_count = 0
                    user.update()
                    openatlas.logger.log('info', 'auth', 'Login of ' + user.username)
                    return redirect(request.args.get('next') or url_for('index'))
                else:
                    openatlas.logger.log('notice', 'auth', 'Inactive login try ' + user.username)
                    flash(_('error inactive'), 'error')
            else:
                openatlas.logger.log('notice', 'auth', 'Wrong password: ' + user.username)
                user.login_failed_count += 1
                user.login_last_failure = datetime.datetime.now()
                user.update()
                flash(_('error wrong password'), 'error')
        else:
            openatlas.logger.log('notice', 'auth', 'Wrong username: ' + request.form['username'])
            flash(_('error username'), 'error')
        return render_template('login/index.html', form=form)
    return render_template('login/index.html', form=form)


@app.route('/password_reset', methods=["GET", "POST"])
def reset_password():
    form = PasswordResetForm()
    if form.validate_on_submit() and session['settings']['mail']:  # pragma: no cover
        user = UserMapper.get_by_email(form.email.data)
        if not user:
            message = 'Password reset for non existing ' + form.email.data
            openatlas.logger.log('info', 'password', message)
            flash(_('error non existing email'), 'error')
        else:
            code = UserMapper.generate_password()
            user.password_reset_code = code
            user.password_reset_date = datetime.datetime.now()
            user.update()
            link = request.scheme + '://' + request.headers['Host']
            link += url_for('reset_confirm', code=code)
            subject = _('mail subject reset_password') + ' ' + session['settings']['site_name']
            body = _('mail received request for') + ' ' + user.username + ' '
            body += _('at') + request.headers['Host'] + '\n' + _('reset password link') + ':\n\n'
            body += link + '\n\n' + _('link is valid for') + ' '
            body += str(session['settings']['reset_confirm_hours']) + ' ' + _('hours')
            if send_mail(subject, body, form.email.data):
                flash(_('A password reset confirm mail send to ') + form.email.data, 'info')
            else:
                flash(_('Failed to send password confirmation mail to ') + form.email.data, 'error')
            return redirect(url_for('login'))
    return render_template('login/reset_password.html', form=form)


@app.route('/reset_confirm/<code>')
def reset_confirm(code):
    user = UserMapper.get_by_reset_code(code)
    if not user:
        openatlas.logger.log('info', 'auth', 'unknown reset code')
        flash(_('invalid password reset confirmation code'), 'error')
        abort(404)
    hours = session['settings']['reset_confirm_hours']
    if datetime.datetime.now() > user.password_reset_date + datetime.timedelta(hours=hours):
        openatlas.logger.log('info', 'auth', 'reset code expired')
        flash(_('This reset confirmation code has expired.'), 'error')
        abort(404)
    password = UserMapper.generate_password()
    user.password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user.password_reset_code = None
    user.password_reset_date = None
    user.update()
    subject = _('mail new password') + ' ' + session['settings']['site_name']
    body = _('mail new password for') + ' ' + user.username + ' '
    body += _('at') + ' ' + request.headers['Host'] + ':\n\n'
    body += uc_first(_('username')) + ': ' + user.username + '\n'
    body += uc_first(_('password')) + ': ' + password + '\n'
    if send_mail(subject, body, user.email):
        flash(_('New password mail to ') + user.email, 'info')
    else:
        flash(_('Failed to send password mail to ') + user.email, 'error')
    return render_template('login/reset_confirm.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    openatlas.logger.log('info', 'auth', 'logout')
    return redirect('/login')

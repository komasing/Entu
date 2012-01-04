# -*- coding: utf-8 -*-

import os
from types import ListType
from pytz.gae import pytz
from google.appengine.api import mail
from google.appengine.api import users
from google.appengine.api import memcache
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp import util
from django.core.validators import email_re
from django.template.defaultfilters import striptags
from django.template import Template
from django.conf import settings
from django.utils import simplejson
import csv
import cStringIO

from datetime import timedelta
import random
import time
import logging
import string
import re

from libraries.gmemsess import *


def Route(url_mapping):
    application = webapp.WSGIApplication(url_mapping, debug=True)
    util.run_wsgi_app(application)


class boRequestHandler(webapp.RequestHandler):
    def __init__(self, *args, **kwargs):
        self.starttime = time.time()
        webapp.RequestHandler.__init__(self, *args, **kwargs)

    def translate(self, key = None):
        user = users.get_current_user()
        sess = Session(self, timeout=86400)
        if user:
            languagefile = 'translations.' + UserPreferences().current.language
        else:
            if 'language' in sess:
                languagefile = 'translations.' + sess['language']
            else:
                languagefile = 'translations.' + SystemPreferences().get('default_language')

        l = __import__(languagefile, globals(), locals(), ['translation'], -1)

        if key:
            if key in l.translation():
                return l.translation()[key].decode('utf8')
            else:
                return key
        else:
            return l.translation()


    def authorize(self, controller=None):
        from database.person import *
        from database.feedback import *

        if db.Query(QuestionaryPerson).filter('person', Person().current).filter('is_completed', False).filter('is_obsolete', False).get():
            path = str(self.request.url)
            Cache().set('redirect_after_feedback', path)
            self.redirect('/feedback')
            return False
        else:
            if controller and users.is_current_user_admin() == False:
                rights = []
                if Person().current.GetRoles():
                    for role in Person().current.GetRoles():
                        rights = rights + role.rights
                    if controller in rights:
                        return True
                    else:
                        return False
                else:
                    return False
            else:
                return True

    def view(self, page_title = '', template_file = None, values={}, main_template=''):
        controllertime = (time.time() - self.starttime)
        logging.debug('Controller: %ss' % round(controllertime, 2))

        user = users.get_current_user()
        email = user.email() if user else ''

        al = AccessLog()
        al.user = email
        al.remote_addr = self.request.remote_addr
        al.path = self.request.path[:500]
        al.query_string = self.request.query_string[:500]
        al.url = self.request.url[:500]
        al.put()

        from database.person import *

        browser = str(self.request.headers['User-Agent'])
        if browser.find('MSIE 5') > -1 or browser.find('MSIE 6') > -1 or browser.find('MSIE 7') > -1 or browser.find('MSIE 8') > -1:
            path = os.path.join(os.path.dirname(__file__), 'errors', 'brauser.html')
            self.response.out.write(template.render(path, {}))
        else:
            values['str'] = self.translate()
            values['system_title'] = SystemPreferences().get('site_title')
            values['system_logo'] = SystemPreferences().get('site_logo_url')
            if page_title:
                values['site_name'] = SystemPreferences().get('site_title') + ' - ' + self.translate(page_title)
                values['page_title'] = self.translate(page_title)
            else:
                values['site_name'] = SystemPreferences().get('site_title')
                values['page_title'] = '&nbsp;'
            values['site_url'] = self.request.headers.get('host')
            values['user'] = Person().current
            values['loginurl'] = users.create_login_url('/')
            values['logouturl'] = users.create_logout_url('/')
            values['version'] = self.request.environ["CURRENT_VERSION_ID"].split('.')[1]

            if main_template:
                main_template_file = open(os.path.join(os.path.dirname(__file__), 'templates', main_template))
                values['main_template'] = Template(main_template_file.read())
                main_template_file.close()

            path = os.path.join(os.path.dirname(__file__), 'templates', template_file)
            self.response.out.write(template.render(path, values))

        viewtime = (time.time() - self.starttime)
        logging.debug('View: %ss' % round((viewtime - controllertime), 2))
        logging.debug('Total: %ss' % round(viewtime, 2))

    def echo(self, string, newline=True):
        self.response.out.write(string)
        if newline:
            self.response.out.write('\n')

    def echo_json(self, dictionary):
        self.response.out.write(simplejson.dumps(dictionary))

    def echo_csv(self, filename, rowslist):
        csvfile = cStringIO.StringIO()
        csvWriter = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
        for row in rowslist:
            csvWriter.writerow(row)
        self.header('Content-Type', 'text/csv; charset=utf-8')
        self.header('Content-Disposition', 'attachment; filename=' + unicode(filename.encode('utf-8'), errors='ignore') + '.csv')
        self.echo(csvfile.getvalue())
        csvfile.close()

    def header(self, key, value):
        self.response.headers[key] = value


class AccessLog(db.Model):
    _version        = db.StringProperty(default='A')
    _created        = db.DateTimeProperty(auto_now_add=True)
    user            = db.StringProperty()
    remote_addr     = db.StringProperty()
    url             = db.TextProperty()
    path            = db.StringProperty()


class ChangeLog(db.Expando):
    _version        = db.StringProperty(default='A')
    _created        = db.DateTimeProperty(auto_now_add=True)
    user            = db.StringProperty()
    kind_name       = db.StringProperty()
    property_name   = db.StringProperty()


class ChangeLogModel(db.Expando):
    _version    = db.StringProperty(default='A')
    _created    = db.DateTimeProperty(auto_now_add=True)
    _changed    = db.DateTimeProperty(auto_now=True)
    _is_deleted = db.BooleanProperty(default=False)

    def put(self, email=None):
        if not email:
            user = users.get_current_user()
            if user:
                email = user.email()
        if self.is_saved():
            old = db.get(self.key())
            for prop_key, prop_value in self.properties().iteritems():
                if old:
                    old_value = prop_value.get_value_for_datastore(old)
                    if old_value == []:
                        old_value = None
                else:
                    old_value = None
                new_value = prop_value.get_value_for_datastore(self)
                if new_value == []:
                    new_value = None
                if old_value != new_value and prop_key != '_version':
                    cl = ChangeLog(parent=self)
                    cl.kind_name = self.kind()
                    cl.property_name = prop_key
                    cl.user = email
                    if old_value != None:
                        cl.old_value = old_value
                    cl.new_value = new_value
                    cl.put()
        return db.Model.put(self)

    @property
    def changed_datetime(self):
        changelog = db.Query(ChangeLog).ancestor(self).order('-_created').get()
        if changelog:
            return changelog.datetime


class SystemPreferences(ChangeLogModel):
    def get(self, key_name):
        sp = SystemPreferences().get_by_key_name(key_name)
        if not sp:
            sp = SystemPreferences(key_name=key_name)
            sp.put()
        return sp.value

    def set(self, key_name, value):
        sp = SystemPreferences().get_by_key_name(key_name)
        if not sp:
            sp = SystemPreferences(key_name=key_name)
        sp.value = value
        sp.put()


class UserPreferences(ChangeLogModel):
    language    = db.StringProperty(default=SystemPreferences().get('default_language'))
    timezone    = db.StringProperty(default=SystemPreferences().get('default_timezone'))

    @property
    def current(self):
        user = users.get_current_user()
        if user:
            email = user.email()
        else:
            email = 'guest'

        u = UserPreferences().get_by_key_name(email)
        if not u:
            u = UserPreferences(key_name=email)
            u.put()
        return u

    def set(self, field, value):
        user = users.get_current_user()
        if user:
            u = UserPreferences().get_by_key_name(user.email())
            if u:
                setattr(u, field, value)
                u.put()


class Cache:
    def set(self, key, value=None, user_specific=True, time=3600):
        if user_specific == True:
            user = users.get_current_user()
            if user:
                key = key + '_' + user.user_id()
            else:
                return False
        if value:
            memcache.delete(key)
            memcache.add(
                key = key,
                value = value,
                time = time
            )
        else:
            memcache.delete(key)
        return value

    def get(self, key, user_specific=True):
        if user_specific == True:
            user = users.get_current_user()
            if user:
                key = key + '_' + user.user_id()
        return memcache.get(key)

def CheckMailAddress(email):
    return email_re.match((email))


def SendMail(to, subject, message=' ', reply_to=None, html=True, attachments=None):
    valid_to = []
    if isinstance(to, ListType):
        for t in to:
            if CheckMailAddress(t):
                valid_to.append(t)
    else:
        if CheckMailAddress(to):
            valid_to.append(to)
    if len(valid_to) > 0:
        m = mail.EmailMessage()
        m.sender = SystemPreferences().get('system_email')
        if reply_to:
            if CheckMailAddress(reply_to):
                m.reply_to = reply_to
        m.bcc = SystemPreferences().get('system_email')
        m.to = valid_to
        m.subject = SystemPreferences().get('system_email_prefix') + subject
        if html == True:
            m.html = message
        else:
            m.body = message
        if attachments:
            m.attachments = attachments
        m.send()

        return True


def StrToList(string):
    if string:
        return [x.strip() for x in string.strip().replace('\n', ' ').replace(',', ' ').replace(';', ' ').split(' ') if len(x.strip()) > 0]
    else:
        return []

def StrToKeyList(string):
    if string:
        strlist = StrToList(string)
        keylist = []
        for key in strlist:
            keylist.append(db.Key(key))
        return keylist
    else:
        return []


def rReplace(s, old, new, occurrence):
    li = s.rsplit(old, occurrence)
    return new.join(li)

def ReplaceUTF(s):
    letters = {'å':'a', 'ä':'a', 'é':'e', 'ö':'o', 'õ':'o', 'ü':'y', 'š':'sh', 'ž':'zh', 'Å':'A', 'Ä':'A', 'É':'E', 'Ö':'O', 'Õ':'O', 'Ü':'Y', 'Š':'SH', 'Ž':'ZH'}
    s = s.encode('utf-8')
    for k, v in letters.iteritems():
        s = s.replace(k, v)
    return s

def StringToSortable(s):
    return re.sub('[%s]' % re.escape(string.punctuation), '', s).lower().strip() if s else ''


def StringToSearchIndex(s):
    if not s:
        return []

    result = []
    s = s.lower()
    wordlist = StrToList(s)
    wordlist.append(s)
    for w in wordlist:
        for i in range(1, len(w)+1):
            result = AddToList(w[:i], result)
    return result


def UtcToLocalDateTime(utc_time):
    utc = pytz.timezone('UTC')
    tz = pytz.timezone(UserPreferences().current.timezone)
    d_tz = utc.normalize(utc.localize(utc_time))
    d_utc = d_tz.astimezone(tz)
    return d_utc


def UtcFromLocalDateTime(local_time):
    utc = pytz.timezone('UTC')
    tz = pytz.timezone(UserPreferences().current.timezone)
    d_tz = tz.normalize(tz.localize(local_time))
    d_utc = d_tz.astimezone(utc)
    return d_utc


def AddToList(s_value=None, s_list=[], unique=True):
    if s_value:
        s_list.append(s_value)
    if unique==True:
        return GetUniqueList(s_list)
    else:
        return s_list


def RemoveFromList(s_value=None, s_list=[], unique=True):
    if s_value in s_list:
        s_list.remove(s_value)
    if unique==True:
        return GetUniqueList(s_list)
    else:
        return s_list


def GetUniqueList(s_list):
    if None in s_list:
        s_list.remove(None)
    return list(set(s_list))


def MergeLists(l1 = [], l2 = []):
    return GetUniqueList(l1 + l2)


def GetListsDiff(l1, l2):
    return list(set(l1).symmetric_difference(set(l2)))


def StripTags(string):
    return striptags(string)


def RandomColor(r1=0, r2=255, g1=0, g2=255, b1=0, b2=255):
    return (('0'+(hex(random.randint(r1, r2))[2:]))[-2:] + ('0'+(hex(random.randint(g1, g2))[2:]))[-2:] + ('0'+(hex(random.randint(b1, b2))[2:]))[-2:]).upper()


def GetFileSize(num):
    for x in ['B','KB','MB','GB','TB']:
        if num < 1024.0:
            return "%3.1f%s" % (num, x)
        num /= 1024.0


def ImageRescale(img_data, width, height, halign='middle', valign='middle'):
    image = images.Image(img_data)

    desired_wh_ratio = float(width) / float(height)
    wh_ratio = float(image.width) / float(image.height)

    if desired_wh_ratio > wh_ratio:
        image.resize(width=width)
        image.execute_transforms()
        trim_y = (float(image.height - height) / 2) / image.height
        if valign == 'top':
            image.crop(0.0, 0.0, 1.0, 1 - (2 * trim_y))
        elif valign == 'bottom':
            image.crop(0.0, (2 * trim_y), 1.0, 1.0)
        else:
            image.crop(0.0, trim_y, 1.0, 1 - trim_y)
    else:
        image.resize(height=height)
        image.execute_transforms()
        trim_x = (float(image.width - width) / 2) / image.width
        if halign == 'left':
            image.crop(0.0, 0.0, 1 - (2 * trim_x), 1.0)
        elif halign == 'right':
            image.crop((2 * trim_x), 0.0, 1.0, 1.0)
        else:
            image.crop(trim_x, 0.0, 1 - trim_x, 1.0)

    return image.execute_transforms()

from pylons import config
import ckan
import json
from ckan.controllers.revision import RevisionController
from ckan.controllers.user import UserController
from ckan.controllers.organization import OrganizationController
from ckan.common import c, _, request, response
import ckan.model as model
import ckan.lib.navl.dictization_functions as dictization_functions
import ckan.authz as authz
import ckan.logic as logic
import ckan.lib.helpers as h
import ckan.lib.authenticator as authenticator
import ckan.lib.base as base
import ckan.lib.csrf_token as csrf_token
import ckan.lib.mailer as mailer

abort = base.abort
render = base.render
check_access = ckan.logic.check_access
NotAuthorized = ckan.logic.NotAuthorized
NotFound = ckan.logic.NotFound
get_action = ckan.logic.get_action

unflatten = dictization_functions.unflatten
DataError = dictization_functions.DataError

UsernamePasswordError = logic.UsernamePasswordError
ValidationError = logic.ValidationError

import logging

log = logging.getLogger(__name__)

import auth


def admin_only(context, data_dict=None):
    return {'success': False, 'msg': 'Access restricted to system administrators'}

def set_repoze_user(user_id):
    '''Set the repoze.who cookie to match a given user_id'''
    if 'repoze.who.plugins' in request.environ:
        rememberer = request.environ['repoze.who.plugins']['friendlyform']
        identity = {'repoze.who.userid': user_id}
        response.headerlist += rememberer.remember(request.environ, identity)

class Apicatalog_RoutesPlugin(ckan.plugins.SingletonPlugin, ckan.lib.plugins.DefaultPermissionLabels):
    ckan.plugins.implements(ckan.plugins.IRoutes, inherit=True)
    ckan.plugins.implements(ckan.plugins.IAuthFunctions)
    ckan.plugins.implements(ckan.plugins.IPermissionLabels)

    # IRoutes

    def before_map(self, m):
        controller = 'ckanext.apicatalog_routes.plugin:Apicatalog_RevisionController'
        m.connect('/revision', action='index', controller=controller)
        m.connect('/revision/list', action='list', controller=controller)
        m.connect('/revision/diff/{id}', action='diff', controller=controller)

        health_controller = 'ckanext.apicatalog_routes.health:HealthController'
        m.connect('/health', action='check', controller=health_controller)

        organization_controller = 'ckanext.apicatalog_routes.plugin:Apicatalog_OrganizationController'
        m.connect('organizations_index', '/organization', controller=organization_controller, action='index')

        extra_information_controller = 'ckanext.apicatalog_routes.plugin:ExtraInformationController'
        m.connect('data_exchange_layer_user_organizations',
                  '/data_exchange_layer_user_organizations',
                  action='data_exchange_layer_user_organizations',
                  controller = extra_information_controller)

        return m

    # IAuthFunctions

    def get_auth_functions(self):
        return {'revision_index': admin_only,
                'revision_list': admin_only,
                'revision_diff': admin_only,
                'package_revision_list': admin_only,
                'package_show': auth.package_show,
                'read_members': auth.read_members,
                'group_edit_permissions': auth.read_members
                }

    # IPermissionLabels

    def get_dataset_labels(self, dataset_obj):

        labels = super(Apicatalog_RoutesPlugin, self).get_dataset_labels(dataset_obj)

        context = {
            'ignore_auth': True
        }

        for user_name in config.get('ckanext.apicatalog_routes.readonly_users', '').split():
            try:
                user_obj = get_action('user_show')(context, {'id': user_name})
                labels.append(u'read_only_admin-%s' % user_obj['id'])
            except NotFound:
                continue

        return labels

    def get_user_dataset_labels(self, user_obj):

        labels = super(Apicatalog_RoutesPlugin, self).get_user_dataset_labels(user_obj)


        if user_obj and user_obj.name in config.get('ckanext.apicatalog_routes.readonly_users', '').split():
            labels.append(u'read_only_admin-%s' % user_obj.id)

        return labels


def auth_context():
    return {'model': ckan.model,
            'user': c.user or c.author,
            'auth_user_obj': c.userobj}


class Apicatalog_RevisionController(RevisionController):

    def index(self):
        try:
            ckan.logic.check_access('revision_index', auth_context())
            return super(Apicatalog_RevisionController, self).index()
        except ckan.logic.NotAuthorized:
            ckan.lib.base.abort(403, _('Not authorized to see this page'))

    def list(self):
        try:
            ckan.logic.check_access('revision_list', auth_context())
            return super(Apicatalog_RevisionController, self).list()
        except ckan.logic.NotAuthorized:
            ckan.lib.base.abort(403, _('Not authorized to see this page'))

    def diff(self, id=None):
        try:
            ckan.logic.check_access('revision_diff', auth_context())
            return super(Apicatalog_RevisionController, self).diff(id=id)
        except ckan.logic.NotAuthorized:
            ckan.lib.base.abort(403, _('Not authorized to see this page'))


class Apicatalog_OrganizationController(OrganizationController):
    def index(self):
        group_type = self._guess_group_type()

        page = h.get_page_number(request.params) or 1
        items_per_page = 21

        context = {'model': model, 'session': model.Session,
                   'user': c.user, 'for_view': True,
                   'with_private': False}

        q = c.q = request.params.get('q', '')
        sort_by = c.sort_by_selected = request.params.get('sort')
        if sort_by is None:
            sort_by = 'title asc'
        try:
            self._check_access('site_read', context)
            self._check_access('group_list', context)
        except NotAuthorized:
            abort(403, _('Not authorized to see this page'))

        # pass user info to context as needed to view private datasets of
        # orgs correctly
        if c.userobj:
            context['user_id'] = c.userobj.id
            context['user_is_admin'] = c.userobj.sysadmin

        data_dict_global_results = {
            'all_fields': False,
            'q': q,
            'sort': sort_by,
            'type': group_type or 'group',
        }
        global_results = self._action('group_list')(context,
                                                    data_dict_global_results)

        data_dict_page_results = {
            'all_fields': True,
            'q': q,
            'sort': sort_by,
            'type': group_type or 'group',
            'limit': items_per_page,
            'offset': items_per_page * (page - 1),
        }
        page_results = self._action('group_list')(context,
                                                  data_dict_page_results)

        c.page = h.Page(
            collection=global_results,
            page=page,
            url=h.pager_url,
            items_per_page=items_per_page,
        )

        c.page.items = page_results
        return render(self._index_template(group_type),
                      extra_vars={'group_type': group_type})


class ExtraInformationController(base.BaseController):

    def data_exchange_layer_user_organizations(self):
        context = {}
        all_organizations = get_action('organization_list')(context, {"all_fields": True})
        packageless_organizations = [o for o in all_organizations if o.get('package_count', 0) == 0]
        response.headers['content-type'] = 'application/json'
        return json.dumps(packageless_organizations)


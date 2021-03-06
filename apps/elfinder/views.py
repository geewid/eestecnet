import json
import os

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, Http404
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views.generic.base import View
from django.views.decorators.csrf import csrf_exempt

from eestecnet.settings.basic import MEDIA_ROOT, MEDIA_URL
from apps.elfinder.models import roots_for_user
from apps.elfinder.volumes.filesystem import ElfinderVolumeLocalFileSystem
from exceptions import ElfinderErrorMessages
from apps.elfinder.connector import ElfinderConnector
from apps.elfinder.conf import settings as ls
from apps.teams.models import Team


def index(request):
    if not request.user.is_authenticated():
        raise PermissionDenied
    if request.is_ajax():
        return render(request, 'elfinder/elfinder-partial.html')
    return render(request, 'elfinder/elfinder.html')


def connector(request):
    if not request.user.is_authenticated():
        raise PermissionDenied
    connector_view = ElfinderConnectorView.as_view()
    response = connector_view(request, optionset="default", start_path="default")
    return response


class ElfinderConnectorView(View):
    """
    Default elfinder backend view
    """

    def render_to_response(self, context, **kwargs):
        """
        It returns a json-encoded response, unless it was otherwise requested
        by the command operation
        """
        kwargs = {}
        additional_headers = {}
        #create response headers
        if 'header' in context:
            for key in context['header']:
                if key == 'Content-Type':
                    kwargs['content_type'] = context['header'][key]
                elif key.lower() == 'status':
                    kwargs['status'] = context['header'][key]
                else:
                    additional_headers[key] = context['header'][key]
            del context['header']

        #return json if not header
        if not 'content_type' in kwargs:
            kwargs['content_type'] = 'application/json'

        if 'pointer' in context:  #return file
            context['pointer'].seek(0)
            kwargs['content'] = context['pointer'].read()
            context['volume'].close(context['pointer'], context['info']['hash'])
        elif 'raw' in context and context['raw'] and 'error' in context and context[
            'error']:  #raw error, return only the error list
            kwargs['content'] = context['error']
        elif kwargs['content_type'] == 'application/json':  #return json
            kwargs['content'] = json.dumps(context)
        else:  #return context as is!
            kwargs['content'] = context

        response = HttpResponse(**kwargs)
        for key, value in additional_headers.items():
            response[key] = value

        return response

    def output(self, cmd, src):
        """
        Collect command arguments, operate and return self.render_to_response()
        """
        args = {}

        for name in self.elfinder.commandArgsList(cmd):
            if name == 'request':
                args['request'] = self.request
            elif name == 'FILES':
                args['FILES'] = self.request.FILES
            elif name == 'upload_path':
                args[name] = src.getlist('upload_path[]')
            elif name == 'targets':
                args[name] = src.getlist('targets[]')
            else:
                arg = name
                if name.endswith('_'):
                    name = name[:-1]
                if name in src:
                    try:
                        args[arg] = src.get(name).strip()
                    except:
                        args[arg] = src.get(name)
        args['debug'] = src['debug'] if 'debug' in src else False

        return self.render_to_response(self.elfinder.execute(cmd, **args))

    def get_command(self, src):
        """
        Get requested command
        """
        try:
            return src['cmd']
        except KeyError:
            return 'open'

    def get_optionset(self, **kwargs):
        set_ = ls.ELFINDER_CONNECTOR_OPTION_SETS[kwargs['optionset']]
        if kwargs['start_path'] != 'default':
            for root in set_['roots']:
                root['startPath'] = kwargs['start_path']
        return set_

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        if not self.request.user.is_authenticated():
            raise PermissionDenied
        if not kwargs['optionset'] in ls.ELFINDER_CONNECTOR_OPTION_SETS:
            raise Http404
        return super(ElfinderConnectorView, self).dispatch(*args, **kwargs)

    def root(self, name):
        return {
            'alias': name,
            'id': name,
            'driver': ElfinderVolumeLocalFileSystem,
            'path': os.path.join(MEDIA_ROOT, unicode(name)),
            'URL': '%s%s/' % (MEDIA_URL, name),
            'uploadMaxSize': '128m',
        }

    def get_optionset_for_user(self, request):
        if request.user.is_superuser:
            roots = [self.root(member.slug) for member in Team.objects.all()]
        elif request.user.is_authenticated():
            roots = [self.root(member.name) for member in
                     request.user.teams_administered()]
            roots.append(self.root('internal'))
        else:
            roots = []
        roots.append(self.root("public"))
        rootsn = roots_for_user(request.user)
        return {'debug': False, 'roots': rootsn}

    def get(self, request, *args, **kwargs):
        """
        used in get method calls
        """

        self.elfinder = ElfinderConnector(self.get_optionset_for_user(request),
                                          request.session)
        return self.output(self.get_command(request.GET), request.GET)

    def post(self, request, *args, **kwargs):
        """
        called in post method calls.
        It only allows for the 'upload' command
        """
        self.elfinder = ElfinderConnector(self.get_optionset_for_user(request),
                                          request.session)
        cmd = self.get_command(request.POST)

        if not cmd in ['upload']:
            self.render_to_response({
                'error': self.elfinder.error(ElfinderErrorMessages.ERROR_UPLOAD,
                                             ElfinderErrorMessages
                                             .ERROR_UPLOAD_TOTAL_SIZE)})

        return self.output(cmd, request.POST)

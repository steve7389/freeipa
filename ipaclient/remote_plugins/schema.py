#
# Copyright (C) 2016  FreeIPA Contributors see COPYING for license
#

import collections
import os.path
import sys
import types

import six

from ipaclient.plugins.rpcclient import rpcclient
from ipalib import parameters, plugable
from ipalib.frontend import Command, Method, Object
from ipalib.output import Output
from ipalib.parameters import DefaultFrom, Flag, Password, Str
from ipalib.text import _
from ipapython.dn import DN
from ipapython.dnsutil import DNSName

if six.PY3:
    unicode = str

_TYPES = {
    'DN': DN,
    'DNSName': DNSName,
    'NoneType': type(None),
    'Sequence': collections.Sequence,
    'bool': bool,
    'dict': dict,
    'int': int,
    'list': list,
    'tuple': tuple,
    'unicode': unicode,
}

_PARAMS = {
    'Decimal': parameters.Decimal,
    'DN': parameters.DNParam,
    'DNSName': parameters.DNSNameParam,
    'bool': parameters.Bool,
    'bytes': parameters.Bytes,
    'datetime': parameters.DateTime,
    'dict': parameters.Dict,
    'int': parameters.Int,
    'str': parameters.Str,
}


class _SchemaCommand(Command):
    def get_options(self):
        skip = set()
        for option in super(_SchemaCommand, self).get_options():
            if option.name in skip:
                continue
            if option.name in ('all', 'raw'):
                skip.add(option.name)
            yield option


class _SchemaMethod(Method, _SchemaCommand):
    _failed_member_output_params = (
        # baseldap
        Str(
            'member',
            label=_("Failed members"),
        ),
        Str(
            'sourcehost',
            label=_("Failed source hosts/hostgroups"),
        ),
        Str(
            'memberhost',
            label=_("Failed hosts/hostgroups"),
        ),
        Str(
            'memberuser',
            label=_("Failed users/groups"),
        ),
        Str(
            'memberservice',
            label=_("Failed service/service groups"),
        ),
        Str(
            'failed',
            label=_("Failed to remove"),
            flags=['suppress_empty'],
        ),
        Str(
            'ipasudorunas',
            label=_("Failed RunAs"),
        ),
        Str(
            'ipasudorunasgroup',
            label=_("Failed RunAsGroup"),
        ),
        # caacl
        Str(
            'ipamembercertprofile',
            label=_("Failed profiles"),
        ),
        Str(
            'ipamemberca',
            label=_("Failed CAs"),
        ),
        # host
        Str(
            'managedby',
            label=_("Failed managedby"),
        ),
        # service
        Str(
            'ipaallowedtoperform_read_keys',
            label=_("Failed allowed to retrieve keytab"),
        ),
        Str(
            'ipaallowedtoperform_write_keys',
            label=_("Failed allowed to create keytab"),
        ),
        # servicedelegation
        Str(
            'failed_memberprincipal',
            label=_("Failed members"),
        ),
        Str(
            'ipaallowedtarget',
            label=_("Failed targets"),
        ),
        # vault
        Str(
            'owner?',
            label=_("Failed owners"),
        ),
    )

    def get_output_params(self):
        seen = set()
        for output_param in super(_SchemaMethod, self).get_output_params():
            seen.add(output_param.name)
            yield output_param
        for output_param in self._failed_member_output_params:
            if output_param.name not in seen:
                yield output_param


class _SchemaObject(Object):
    pass


class _SchemaPlugin(object):
    bases = None
    schema_key = None

    def __init__(self, name):
        self.name = name
        self.version = '1'
        self.full_name = '{}/{}'.format(self.name, self.version)
        self.__class = None

    def _create_default_from(self, api, name, keys):
        cmd_name = self.name

        def get_default(*args):
            kw = dict(zip(keys, args))
            result = api.Command.command_defaults(
                unicode(cmd_name),
                params=[unicode(name)],
                kw=kw,
            )['result']
            return result.get(name)

        if keys:
            def callback(*args):
                return get_default(*args)
        else:
            def callback():
                return get_default()

        callback.__name__ = '{0}_{1}_default'.format(cmd_name, name)

        return DefaultFrom(callback, *keys)

    def _create_param(self, api, schema):
        name = str(schema['name'])
        type_name = str(schema['type'])
        sensitive = schema.get('sensitive', False)

        if type_name == 'str' and sensitive:
            cls = Password
            sensitive = False
        elif (type_name == 'bool' and
                'default' in schema and
                schema['default'] == [u'False']):
            cls = Flag
            del schema['default']
        else:
            try:
                cls = _PARAMS[type_name]
            except KeyError:
                cls = Str

        kwargs = {}
        default = None

        for key, value in schema.items():
            if key in ('alwaysask',
                       'doc',
                       'label',
                       'multivalue',
                       'no_convert',
                       'option_group',
                       'required'):
                kwargs[key] = value
            elif key in ('cli_metavar',
                         'cli_name'):
                kwargs[key] = str(value)
            elif key == 'confirm' and issubclass(cls, Password):
                kwargs[key] = value
            elif key == 'default':
                default = value
            elif key == 'default_from_param':
                keys = tuple(str(k) for k in value)
                kwargs['default_from'] = (
                    self._create_default_from(api, name, keys))
            elif key in ('exclude',
                         'include'):
                kwargs[key] = tuple(str(v) for v in value)

        if default is not None:
            tmp = cls(name, **dict(kwargs, no_convert=False))
            if tmp.multivalue:
                default = tuple(tmp._convert_scalar(d) for d in default)
            else:
                default = tmp._convert_scalar(default[0])
            kwargs['default'] = default

        if 'default' in kwargs or 'default_from' in kwargs:
            kwargs['autofill'] = not kwargs.pop('alwaysask', False)

        param = cls(name, **kwargs)

        if sensitive:
            object.__setattr__(param, 'password', True)

        return param

    def _create_class(self, api, schema):
        class_dict = {}

        class_dict['name'] = self.name
        if 'doc' in schema:
            class_dict['doc'] = schema['doc']
        if 'topic_topic' in schema:
            class_dict['topic'] = str(schema['topic_topic'])
        else:
            class_dict['topic'] = None

        class_dict['takes_params'] = tuple(self._create_param(api, s)
                                           for s in schema.get('params', []))

        return self.name, self.bases, class_dict

    def __call__(self, api):
        if self.__class is None:
            schema = api._schema[self.schema_key][self.name]
            name, bases, class_dict = self._create_class(api, schema)
            self.__class = type(name, bases, class_dict)

        return self.__class(api)


class _SchemaCommandPlugin(_SchemaPlugin):
    bases = (_SchemaCommand,)
    schema_key = 'commands'

    def _create_output(self, api, schema):
        if schema.get('multivalue', False):
            type_type = (tuple, list)
            if not schema.get('required', True):
                type_type = type_type + (type(None),)
        else:
            try:
                type_type = _TYPES[schema['type']]
            except KeyError:
                type_type = None
            else:
                if not schema.get('required', True):
                    type_type = (type_type, type(None))

        kwargs = {}
        kwargs['type'] = type_type

        if 'doc' in schema:
            kwargs['doc'] = schema['doc']

        if schema.get('no_display', False):
            kwargs['flags'] = ('no_display',)

        return Output(str(schema['name']), **kwargs)

    def _create_class(self, api, schema):
        name, bases, class_dict = (
            super(_SchemaCommandPlugin, self)._create_class(api, schema))

        if 'obj_class' in schema or 'attr_name' in schema:
            bases = (_SchemaMethod,)

        if 'obj_class' in schema:
            class_dict['obj_name'] = str(schema['obj_class'])
        if 'attr_name' in schema:
            class_dict['attr_name'] = str(schema['attr_name'])
        if 'exclude' in schema and u'cli' in schema['exclude']:
            class_dict['NO_CLI'] = True

        args = set(str(s['name']) for s in schema['params']
                   if s.get('positional', s.get('required', True)))
        class_dict['takes_args'] = tuple(
            p for p in class_dict['takes_params'] if p.name in args)
        class_dict['takes_options'] = tuple(
            p for p in class_dict['takes_params'] if p.name not in args)
        del class_dict['takes_params']

        class_dict['has_output'] = tuple(
            self._create_output(api, s) for s in schema['output'])

        return name, bases, class_dict


class _SchemaObjectPlugin(_SchemaPlugin):
    bases = (_SchemaObject,)
    schema_key = 'classes'


def get_package(api):
    try:
        schema = api._schema
    except AttributeError:
        client = rpcclient(api)
        client.finalize()

        client.connect(verbose=False)
        try:
            schema = client.forward(u'schema', version=u'2.170')['result']
        finally:
            client.disconnect()

        for key in ('commands', 'classes', 'topics'):
            schema[key] = {str(s.pop('name')): s for s in schema[key]}

        object.__setattr__(api, '_schema', schema)

    fingerprint = str(schema['fingerprint'])
    package_name = '{}${}'.format(__name__, fingerprint)
    package_dir = '{}${}'.format(os.path.splitext(__file__)[0], fingerprint)

    try:
        return sys.modules[package_name]
    except KeyError:
        pass

    package = types.ModuleType(package_name)
    package.__file__ = os.path.join(package_dir, '__init__.py')
    package.modules = ['plugins']
    sys.modules[package_name] = package

    module_name = '.'.join((package_name, 'plugins'))
    module = types.ModuleType(module_name)
    module.__file__ = os.path.join(package_dir, 'plugins.py')
    module.register = plugable.Registry()
    for key, plugin_cls in (('commands', _SchemaCommandPlugin),
                            ('classes', _SchemaObjectPlugin)):
        for name in schema[key]:
            plugin = plugin_cls(name)
            plugin = module.register()(plugin)
            setattr(module, name, plugin)
    sys.modules[module_name] = module

    for name, topic in six.iteritems(schema['topics']):
        module_name = '.'.join((package_name, name))
        try:
            module = sys.modules[module_name]
        except KeyError:
            module = sys.modules[module_name] = types.ModuleType(module_name)
            module.__file__ = os.path.join(package_dir, '{}.py'.format(name))
        module.__doc__ = topic.get('doc')
        if 'topic_topic' in topic:
            module.topic = str(topic['topic_topic'])
        else:
            module.topic = None

    return package

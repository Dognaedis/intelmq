# -*- coding: utf-8 -*-
"""
Messages are the information packages in pipelines.

Use MessageFactory to get a Message object (types Report and Event).
"""
import hashlib
import json
import re
import warnings

import intelmq.lib.exceptions as exceptions
import intelmq.lib.harmonization
from intelmq import HARMONIZATION_CONF_FILE
from intelmq.lib import utils

__all__ = ['Event', 'Message', 'MessageFactory', 'Report']


harm_config = utils.load_configuration(HARMONIZATION_CONF_FILE)


class MessageFactory(object):
    """
    unserialize: JSON encoded message to object
    serialize: object to JSON encoded object
    """

    @staticmethod
    def from_dict(message):
        """
        Takes dictionary Message object, returns instance of correct class.

        The class is determined by __type attribute.
        """
        try:
            class_reference = getattr(intelmq.lib.message, message["__type"])
        except AttributeError:
            raise exceptions.InvalidArgument('__type',
                                             got=message["__type"],
                                             expected=list(harm_config.keys()),
                                             docs=HARMONIZATION_CONF_FILE)
        del message["__type"]
        return class_reference(message, auto=True)

    @staticmethod
    def unserialize(raw_message, harmonization=None):
        """
        Takes JSON-encoded Message object, returns instance of correct class.

        The class is determined by __type attribute.
        """
        message = Message.unserialize(raw_message)
        try:
            class_reference = getattr(intelmq.lib.message, message["__type"])
        except AttributeError:
            raise exceptions.InvalidArgument('__type',
                                             got=message["__type"],
                                             expected=list(harm_config.keys()),
                                             docs=HARMONIZATION_CONF_FILE)
        del message["__type"]
        return class_reference(message, auto=True, harmonization=harmonization)

    @staticmethod
    def serialize(message):
        """
        Takes instance of message-derived class and makes JSON-encoded Message.

        The class is saved in __type attribute.
        """
        raw_message = Message.serialize(message)
        return raw_message


class Message(dict):

    def __init__(self, message=(), auto=False, harmonization=None):
        try:
            classname = message['__type'].lower()
            del message['__type']
        except (KeyError, TypeError):
            classname = self.__class__.__name__.lower()

        if harmonization is None:
            try:
                self.harmonization_config = harm_config[classname]
            except KeyError:
                raise exceptions.InvalidArgument('__type',
                                                 got=classname,
                                                 expected=list(harm_config.keys()),
                                                 docs=HARMONIZATION_CONF_FILE)
        else:
            self.harmonization_config = harmonization[classname]

        super(Message, self).__init__()
        if isinstance(message, dict):
            iterable = message.items()
        elif isinstance(message, tuple):
            iterable = message
        for key, value in iterable:
            try:
                self.add(key, value, sanitize=False)
            except exceptions.InvalidValue:
                self.add(key, value, sanitize=True)

    def __setitem__(self, key, value):
        self.add(key, value)

    def add(self, key, value, sanitize=True, force=False, ignore=()):
        if not force and key in self:
            raise exceptions.KeyExists(key)

        if value is None or value == "":
            return

        if value in ["-", "N/A"]:
            return

        if not self.__is_valid_key(key):
            raise exceptions.InvalidKey(key)

        if ignore:
            warnings.warn('The ignore-argument will be removed in 1.0.',
                          DeprecationWarning)

        try:
            if value in ignore:
                return
        except TypeError:
            raise exceptions.InvalidArgument('ignore',
                                             got=type(ignore),
                                             expected='list or tuple')

        if sanitize and not key == '__type':
            old_value = value
            value = self.__sanitize_value(key, value)
            if value is None:
                raise exceptions.InvalidValue(key, old_value)

        valid_value = self.__is_valid_value(key, value)
        if not valid_value[0]:
            raise exceptions.InvalidValue(key, value, reason=valid_value[1])

        super(Message, self).__setitem__(key, value)

    def update(self, key, value, sanitize=True):
        warnings.warn('update(...) will be changed to dict.update() in 1.0. '
                      'Use change(key, value, sanitize) instead.',
                      DeprecationWarning)
        self.change(key, value, sanitize)

    def change(self, key, value, sanitize=True):
        if key not in self:
            raise exceptions.KeyNotExists(key)
        self.add(key, value, force=True, sanitize=sanitize)

    def contains(self, key):
        return key in self

    def finditems(self, keyword):
        for key, value in super(Message, self).items():
            if key.startswith(keyword):
                yield key, value

    def copy(self):
        class_ref = self.__class__.__name__
        self['__type'] = class_ref
        retval = getattr(intelmq.lib.message,
                         class_ref)(super(Message, self).copy())
        del self['__type']
        return retval

    def deep_copy(self):
        return MessageFactory.unserialize(MessageFactory.serialize(self))

    def __unicode__(self):
        return self.serialize()

    def __str__(self):
        return self.serialize()

    def serialize(self):
        self['__type'] = self.__class__.__name__
        json_dump = utils.decode(json.dumps(self))
        del self['__type']
        return json_dump

    @staticmethod
    def unserialize(message_string):
        message = json.loads(message_string)
        return message

    def __is_valid_key(self, key):
        if key in self.harmonization_config or key == '__type':
            return True
        return False

    def __is_valid_value(self, key, value):
        if key == '__type':
            return (True, )
        config = self.__get_type_config(key)
        class_reference = getattr(intelmq.lib.harmonization, config['type'])
        if not class_reference().is_valid(value):
            return (False, 'is_valid returned False.')
        if 'length' in config:
            length = len(str(value))
            if not length <= config['length']:
                return (False, 'too long: {} > {}.'.format(length,
                                                           config['length']))
        if 'regex' in config:
            if not re.search(config['regex'], str(value)):
                return (False, 'regex did not match.')
        if 'iregex' in config:
            if not re.search(config['iregex'], str(value), re.IGNORECASE):
                return (False, 'regex (case insensitive) did not match.')
        return (True, )

    def __sanitize_value(self, key, value):
        class_name = self.__get_type_config(key)['type']
        class_reference = getattr(intelmq.lib.harmonization, class_name)
        return class_reference().sanitize(value)

    def __get_type_config(self, key):
        class_name = self.harmonization_config[key]
        return class_name

    def __hash__(self):
        return int(self.hash(), 16)

    def hash(self, blacklist=frozenset()):
        """Return a sha256 hash of the message as a hexadecimal string.
        The hash is computed over almost all key/value pairs. The only
        keys omitted are 'time.observation' and all keys contained in
        the optional blacklist parameter. If given, the blacklist
        parameter should be a set.
        """
        event_hash = hashlib.sha256()

        for key, value in sorted(self.items()):
            if "time.observation" == key or key in blacklist:
                continue

            event_hash.update(utils.encode(key))
            event_hash.update(b"\xc0")
            event_hash.update(utils.encode(repr(value)))
            event_hash.update(b"\xc0")

        return event_hash.hexdigest()

    def to_dict(self, hierarchical=False, with_type=False):
        json_dict = dict()

        if with_type:
            self['__type'] = self.__class__.__name__

        for key, value in self.items():
            if hierarchical:
                subkeys = key.split('.')
            else:
                subkeys = [key]
            json_dict_fp = json_dict

            for subkey in subkeys:
                if subkey == subkeys[-1]:
                    json_dict_fp[subkey] = value
                    break

                if subkey not in json_dict_fp:
                    json_dict_fp[subkey] = dict()

                json_dict_fp = json_dict_fp[subkey]

        if with_type:
            del self['__type']

        return json_dict

    def to_json(self, hierarchical=False, with_type=False):
        json_dict = self.to_dict(hierarchical=hierarchical, with_type=with_type)
        return json.dumps(json_dict, ensure_ascii=False)


class Event(Message):

    def __init__(self, message=(), auto=False, harmonization=None):
        """
        Parameters
        ----------
        message : dict
            Give a report and feed.name, feed.url and
            time.observation will be used to construct the Event if given.
            If it's another type, the value is given to dict's init
        """
        if isinstance(message, Report):
            template = {}
            if 'feed.accuracy' in message:
                template['feed.accuracy'] = message['feed.accuracy']
            if 'feed.code' in message:
                template['feed.code'] = message['feed.code']
            if 'feed.name' in message:
                template['feed.name'] = message['feed.name']
            if 'feed.provider' in message:
                template['feed.provider'] = message['feed.provider']
            if 'feed.url' in message:
                template['feed.url'] = message['feed.url']
            if 'rtir_id' in message:
                template['rtir_id'] = message['rtir_id']
            if 'time.observation' in message:
                template['time.observation'] = message['time.observation']
        else:
            template = message
        super(Event, self).__init__(template, auto, harmonization)


class Report(Message):

    def __init__(self, message=(), auto=False, harmonization=None):
        """
        Parameters
        ----------
        message : dict
            Passed along to Message's and dict's init
        auto : boolean
            if false (default), time.observation is automatically added.
        """
        super(Report, self).__init__(message, auto, harmonization)
        if not auto and 'time.observation' not in self:
            time_observation = intelmq.lib.harmonization.DateTime().generate_datetime_now()
            self.add('time.observation', time_observation, sanitize=False)

    def copy(self):
        retval = super(Report, self).copy()
        if 'time.observation' in retval and 'time.observation' not in self:
            del retval['time.observation']
        return retval

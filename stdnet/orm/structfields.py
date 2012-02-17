from stdnet.exceptions import *
from stdnet.utils import encoders

from .fields import Field
from . import related
from .struct import *


__all__ = ['StructureField',
           'StringField',
           'SetField',
           'ListField',
           'HashField',
           'TimeSeriesField']


class StructureFieldProxy(object):
    
    def __init__(self, name, factory, cache_name, pickler, value_pickler):
        self.name = name
        self.factory = factory
        self.cache_name = cache_name
        self.pickler = pickler
        self.value_pickler = value_pickler
        
    def __get__(self, instance, instance_type = None):
        if instance is None:
            return self
        if instance.id is None:
            raise StructureFieldError('id for %s is not available.\
 Call save on instance before accessing %s.' % (instance._meta,self.name))
        cache_name = self.cache_name
        cache_val = None
        try:
            cache_val = getattr(instance, cache_name)
            if not isinstance(cache_val,Structure):
                raise AttributeError()
            elif cache_val.session != instance.session and instance.session:
                instance.session.add(cache_val)
            return cache_val
        except AttributeError:
            structure = self.get_structure(instance)
            setattr(instance, cache_name, structure)
            if cache_val is not None:
                structure.set_cache(cache_val)
            return structure
        
    def get_structure(self, instance):
        session = instance.session
        backend = session.backend
        id = backend.basekey(instance._meta, 'obj', instance.id, self.name)
        return self.factory(id = id,
                            instance = instance,
                            pickler = self.pickler,
                            value_pickler = self.value_pickler)


class StructureField(Field):
    '''Virtual class for fields which are proxies to remote
:ref:`data structures <structures-backend>` such as :class:`stdnet.List`,
:class:`stdnet.Set`, :class:`stdnet.OrderedSet` and :class:`stdnet.HashTable`.

Sometimes you want to structure your data model without breaking it up
into multiple entities. For example, you might want to define model
that contains a list of messages an instance receive::

    from stdnet import orm
    
    class MyModel(orm.StdModel):
        ...
        messages = orm.ListField()

By defining structured fields in a model, an instance of that model can access
a stand alone structure in the back-end server with very little effort.


:parameter model: an optional :class:`stdnet.orm.StdModel` class. If
    specified, the structured will contains ids of instances of the model.
    It is saved in the :attr:`relmodel` attribute.
    
.. attribute:: relmodel

    Optional :class:`stdnet.otm.StdModel` class contained in the structure.
    It can also be specified as a string.
    
.. attribute:: pickler

    an instance of :class:`stdnet.utils.encoders.Encoder` used to serialize
    and userialize data. It contains the ``dumps`` and ``loads`` methods.
    
    Default :class:`stdnet.utils.encoders.Json`.
    
.. attribute:: value_pickler

    Same as the :attr:`pickler` attribute, this serializer is applaied to values
    (used by hash table)
    
    Default: ``None``.
'''
    default_pickler = None
    default_value_pickler = encoders.Json()
    
    def __init__(self,
                 model = None,
                 pickler = None,
                 value_pickler = None,
                 required = False,
                 **kwargs):
        # Force required to be false
        super(StructureField,self).__init__(required = False, **kwargs)
        self.relmodel = model
        self.index = False
        self.unique = False
        self.primary_key = False
        self.pickler = pickler
        self.value_pickler = value_pickler
        
    def register_with_model(self, name, model):
        super(StructureField,self).register_with_model(name, model)
        if self.relmodel:
            related.load_relmodel(self,self._set_relmodel)
        else:
            self._register_with_model()
    
    def _set_relmodel(self, relmodel):
        self.relmodel = relmodel
        self._register_with_model()
        
    def _register_with_model(self):
        data_structure_class = self.structure_class()
        self.value_pickler = self.value_pickler or\
                                            data_structure_class.value_pickler
        self.pickler = self.pickler or data_structure_class.pickler or\
                            self.default_pickler
        if not self.value_pickler:
            if self.relmodel:
                self.value_pickler = related.ModelFieldPickler(self.relmodel)
            else:
                self.value_pickler = self.default_value_pickler
        setattr(self.model,
                self.name,
                StructureFieldProxy(self.name,
                                    data_structure_class,
                                    self.get_cache_name(),
                                    pickler = self.pickler,
                                    value_pickler = self.value_pickler))

    def add_to_fields(self):
        self.model._meta.multifields.append(self)
        
    def to_python(self, instance):
        return None
    
    def id(self, obj):
        return getattr(obj,self.attname).id

    def todelete(self):
        return True
    
    def structure_class(self):
        raise NotImplementedError

    def set_cache(self, instance, data):
        setattr(instance,self.get_cache_name(),data)
        

class SetField(StructureField):
    '''A field maintaining an unordered collection of values. It is initiated
without any argument other than an optional model class.
When accessed from the model instance, it returns an instance of
:class:`stdnet.Set` structure. For example::

    class User(orm.StdModel):
        username  = orm.AtomField(unique = True)
        password  = orm.AtomField()
        following = orm.SetField(model = 'self')
    
It can be used in the following way::
    
    >>> user = User(username = 'lsbardel', password = 'mypassword').save()
    >>> user2 = User(username = 'pippo', password = 'pippopassword').save()
    >>> user.following.add(user2)
    >>> user.save()
    >>> user2 in user.following
    True
    '''
    def structure_class(self):
        return Zset if self.ordered else Set
    

class ListField(StructureField):
    '''A field maintaining a list of values.
When accessed from the model instance,
it returns an instance of :class:`stdnet.List` structure. For example::

    class UserMessage(orm.StdModel):
        user = orm.SymbolField()
        messages = orm.ListField()
    
Lets register it with redis::

    >>> orm.register(UserMessage,''redis://127.0.0.1:6379/?db=11')
    'redis db 7 on 127.0.0.1:6379'
    
Can be used as::

    >>> m = UserMessage(user = 'pippo').save()
    >>> m.messages.push_back("adding my first message to the list")
    >>> m.messages.push_back("ciao")
    >>> m.save()
    >>> type(u.messages)
    <class 'stdnet.backends.structures.structredis.List'>
    >>> u.messages.size()
    2
    '''
    type = 'list'
    def structure_class(self):
        return List        


class HashField(StructureField):
    '''A Hash table field, the networked equivalent of a python dictionary.
Keys are string while values are string/numeric.
it returns an instance of :class:`stdnet.HashTable` structure.
'''
    type = 'hash'
    default_pickler = encoders.NoEncoder()
    default_value_pickler = encoders.Json()
    
    def _install_encoders(self):
        if self.relmodel and not self.value_pickler:
            self.value_pickler = related.ModelFieldPickler(relmodel)

    def structure_class(self):
        return HashTable


class TimeSeriesField(HashField):
    '''A timeseries field based on TS data structure in Redis.
To be used with subclasses of :class:`TimeSeriesBase`'''
    default_pickler = None
    
    def structure_class(self):
        return TS
        
        
class StringField(StructureField):
    default_value_pickler = None
    
    def structure_class(self):
        return String
    
'''\
This is a pure python implementation of the :class:`stdnet.orm.SearchEngine`
interface.

This is not intended to be the most full-featured text search available, but
it does the job. For that, look into Sphinx_, Solr, or other alternatives.

Usage
===========

Somewhere in your application create the search engine singletone::

    from stdnet.apps.searchengine import SearchEngine
     
    engine = SearchEngine(...)
 
The engine works by registering models to it via the
:meth:`stdnet.orm.SearchEngine.register` method.
For example::

    engine.register(MyModel)

From now on, every time and instance of ``MyModel`` is created,
the search engine will updated its indexes.

To search, you use the :class:`stdnet.orm.Query` API::

    query = self.session().query(MyModel)
    search_result = query.search(sometext)
    
If you would like to limit the search to some specified models::

    search_result = engine.search(sometext, include = (model1,model2,...))
    
    
.. _Sphinx: http://sphinxsearch.com/
'''
import re
from inspect import isclass

from stdnet import orm
from stdnet.utils import to_string, iteritems

from .models import Word, WordItem, Tag
from . import processors

    
class SearchEngine(orm.SearchEngine):
    """A python implementation for the :class:`stdnet.orm.SearchEngine`
driver.
    
:parameter min_word_length: minimum number of words required by the engine
                            to work.

                            Default ``3``.
                            
:parameter stop_words: list of words not included in the search engine.

                       Default ``stdnet.apps.searchengine.ignore.STOP_WORDS``
                          
:parameter metaphone: If ``True`` the double metaphone_ algorithm will be
    used to store and search for words. The metaphone should be the last
    world middleware to be added.
                      
    Default ``True``.

:parameter splitters: string whose characters are used to split text
                      into words. If this parameter is set to `"_-"`,
                      for example, than the word `bla_pippo_ciao-moon` will
                      be split into `bla`, `pippo`, `ciao` and `moon`.
                      Set to empty string for no splitting.
                      Splitting will always occur on white spaces.
                      
                      Default
                      ``stdnet.apps.searchengine.ignore.PUNCTUATION_CHARS``.

.. _metaphone: http://en.wikipedia.org/wiki/Metaphone
"""
    REGISTERED_MODELS = {}
    ITEM_PROCESSORS = []
    
    def __init__(self, min_word_length = 3, stop_words = None,
                 metaphone = True, stemming = True,
                 splitters = None):
        super(SearchEngine,self).__init__()
        self.MIN_WORD_LENGTH = min_word_length
        splitters = splitters if splitters is not None else\
                    processors.PUNCTUATION_CHARS
        if splitters: 
            self.punctuation_regex = re.compile(\
                                    r"[%s]" % re.escape(splitters))
        else:
            self.punctuation_regex = None
        # The stop words middleware is only used for the indexing part
        self.add_word_middleware(processors.stopwords(stop_words), False)
        if stemming:
            self.add_word_middleware(processors.stemming_processor)
        if metaphone:
            self.add_word_middleware(processors.tolerant_metaphone_processor)
        
    def split_text(self, text):
        if self.punctuation_regex:
            text = self.punctuation_regex.sub(" ", text)
        mwl = self.MIN_WORD_LENGTH
        for word in text.split():
            if len(word) >= mwl:
                word = word.lower()
                yield word
    
    def flush(self, full = False):
        WordItem.objects.flush()
        if full:
            Word.objects.flush()
        
    def add_item(self, item, words, session):    
        link = self._link_item_and_word
        for word,count in iteritems(words):
            session.add(link(item, word, count))
    
    def remove_item(self, item_or_model, ids = None, session = None):
        '''\
Remove indexes for *item*.

:parameter item: an instance of a :class:`stdnet.orm.StdModel`.        
'''
        session = session or item_or_model.session
        query = session.query(WordItem)
        if isclass(item_or_model):
            wi = query.filter(model_type = item_or_model)
            if ids is not None:
                wi = wi.filter(object_id__in = ids)
        else:
            wi = query.filter(model_type = item_or_model.__class__,
                              object_id = item_or_model.id)
        session.delete(wi)
    
    def words(self, text, for_search = False):
        '''Given a text string,
return a list of :class:`Word` instances associated with it.
The word items can be used to perform search on registered models.'''
        texts = self.words_from_text(text,for_search)
        if texts:
            return Word.objects.filter(id__in = texts).all()
    
    def search(self, text, include = None, exclude = None):
        '''Full text search'''
        return list(self.items_from_text(text,include,exclude))
    
    def search_model(self, q, text, lookup = 'in'):
        '''Implements :meth:`stdnet.orm.SearchEngine.search_model`.
It return a new :class:`stdnet.orm.QueryElem` instance from
the input :class:`Query` and the *text* to search.'''
        words = self.words(text, for_search=True)
        if words is None:
            return q
        elif not words:
            return orm.EmptyQuery(q.meta,q.session)
        
        query = WordItem.objects.filter(model_type = q.model)
        qs =  []
        for word in words:
            qs.append(query.filter(word = word).get_field('object_id'))
            
        if len(qs) > 1:
            if lookup == 'in':
                qs = orm.intersect(qs)
            elif lookup == 'contains':
                qs = orm.union(qs)
            else:
                raise valueError('Unknown lookup "{0}"'.format(lookup))
        else:
            qs = qs[0]
        return orm.intersect((q,qs))
        
    def add_tag(self, item, text):
        '''Add a tag to an object.
    If the object already has the tag associated with it do nothing.
    
    :parameter item: instance of :class:`stdnet.orm.StdModel`.
    :parameter tag: a string for the tag name or a :class:`Tag` instance.
    
    It returns an instance of :class:`TaggedItem`.
    '''
        linked = []
        link = self._link_item_and_word
        for word in self.words_from_text(text):
            ctag = self.get_or_create(word, tag = True)
            linked.append(link(item, ctag))
        return linked
    
    def tags_for_item(self, item):
        return list(self.words_for_item(item, True))                

    def alltags(self, *models):
        '''Return a dictionary where keys are tag names and values are integers
        representing how many times the corresponding tag has been used against
        the Model classes in question.'''
        tags = {}
        for wi in WordItem.objects.filter(model_type__in = models):
            word = wi.word
            if word.tag:
                if word in tags:
                    tags[word] += 1
                else:
                    tags[word] = 1
        return tags

    # INTERNALS

    def words_for_item(self, item, tag = None):
        wis = WordItem.objects.filter(model_type = item.__class__,\
                                      object_id = item.id)
        if tag is not None:
            for wi in wis:
                if wi.word.tag == tag:
                    yield wi.word
        else:
            for wi in wis:
                yield wi.word
                    
    def _link_item_and_word(self, item, word, count = 1, tag = False):
        w = self.get_or_create(word, tag = tag, session = item.session)
        return WordItem(word = w,
                        model_type = item.__class__,
                        object_id = item.id,
                        count = count)
    
    def item_field_iterator(self, item):
        for processor in self.ITEM_PROCESSORS:
            result = processor(item)
            if result:
                return result
        raise ValueError(
                'Cound not iterate through item {0} fields'.format(item))
    
    def get_or_create(self, word, tag = False, session = None):
        # Internal for adding or creating words
        try:
            w = Word.objects.get(id = word)
            if tag and not w.tag:
                w.tag = True
                return w.save()
            else:
                return w
        except Word.DoesNotExist:
            # we need to create a new word. Since Words have id known even when
            # not persistent, we cann add the Word to the current session
            # without saving
            w = Word(id = word, tag = tag)
            if session:
                return session.add(w)
            else:
                return w.save()
   




from collections import defaultdict

from etc.toolbox import get_model_class_from_settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import signals, Count, Model

from sitecats import settings


def get_category_model():
    """Returns the Category model, set for the project."""
    return get_model_class_from_settings(settings, 'MODEL_CATEGORY')


def get_tie_model():
    """Returns the Tie model, set for the project."""
    return get_model_class_from_settings(settings, 'MODEL_TIE')


class Cache(object):

    # Sitecats objects are stored in Django cache for a year (60 * 60 * 24 * 365 = 31536000 sec).
    # Cache is only invalidated on sitecats Category model save/delete.
    CACHE_TIMEOUT = 31536000
    CACHE_ENTRY_NAME = 'sitecats'

    CACHE_NAME_IDS = 'ids'
    CACHE_NAME_ALIASES = 'aliases'
    CACHE_NAME_PARENTS = 'parents'

    def __init__(self):
        self._cache = None
        # Listen for signals from the models.
        category_model = get_category_model()
        signals.post_save.connect(self._cache_empty, sender=category_model)
        signals.post_delete.connect(self._cache_empty, sender=category_model)

    def _cache_init(self):
        """Initializes local cache from Django cache if required."""
        cache_ = cache.get(self.CACHE_ENTRY_NAME)

        if cache_ is None:
            categories = get_category_model().objects.order_by('sort_order')

            ids = {category.id: category for category in categories}
            aliases = {category.alias: category for category in categories if category.alias}

            parent_to_children = defaultdict(list)
            for category in categories:
                parent_category = ids.get(category.parent_id, False)
                parent_alias = None
                if parent_category:
                    parent_alias = parent_category.alias
                parent_to_children[parent_alias].append(category.id)

            cache_ = {
                self.CACHE_NAME_IDS: ids,
                self.CACHE_NAME_PARENTS: parent_to_children,
                self.CACHE_NAME_ALIASES: aliases
            }

            cache.set(self.CACHE_ENTRY_NAME, cache_, self.CACHE_TIMEOUT)

        self._cache = cache_

    def _cache_empty(self, **kwargs):
        """Empties cached sitecats data."""
        self._cache = None
        cache.delete(self.CACHE_ENTRY_NAME)

    def _cache_get_entry(self, entry_name, key, default=False):
        """Returns cache entry parameter value by its name."""
        return self._cache[entry_name].get(key, default)

    def _populate_tags_data(self, tags, target_object):
        for tag in tags:
            # Attach category data from cache to prevent db hits.
            category = self.get_category_by_id(tag['category_id'])
            tag.update(category.__dict__)
            tag['absolute_url'] = category.get_absolute_url(target_object)
        return tags

    def get_child_ids(self, parent_alias):
        self._cache_init()
        return self._cache_get_entry(self.CACHE_NAME_PARENTS, parent_alias, [])

    def is_child(self, parent_alias, child_id):
        return child_id in self.get_child_ids(parent_alias)

    def get_category_by_alias(self, alias):
        self._cache_init()
        return self._cache_get_entry(self.CACHE_NAME_ALIASES, alias)

    def get_category_by_id(self, id):
        self._cache_init()
        return self._cache_get_entry(self.CACHE_NAME_IDS, id)

    def find_category(self, parent_alias, title):
        found = None
        child_ids = self.get_child_ids(parent_alias)
        for cid in child_ids:
            category = self.get_category_by_id(cid)
            if category.title.lower() == title.lower():  # Case independent.
                found = category
                break
        return found

    def get_categories(self, parent_alias=None, target_object=None):
        child_ids = self.get_child_ids(parent_alias)
        if target_object is None:  # No filtering by object, list all known categories.
            return [self.get_category_by_id(cid) for cid in child_ids]
        else:
            filter_kwargs = {}
            if child_ids:
                filter_kwargs.update({'category_id__in': child_ids})

            filter_kwargs.update({
                'content_type': ContentType.objects.get_for_model(target_object),
                'object_id': target_object.id
            })

            items = list(get_tie_model().objects.filter(**filter_kwargs).values('category_id').annotate(ties_num=Count('category')))
            return self._populate_tags_data(items, target_object)

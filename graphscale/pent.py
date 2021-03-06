from collections import OrderedDict
import inspect
from typing import Any, Dict, List, Sequence, Type, TypeVar, cast
from uuid import UUID

from aiodataloader import DataLoader

from graphscale import check
from graphscale.kvetch import EdgeData, Kvetch, Schema
from graphscale.utils import reverse_dict


class PentConfig:
    def __init__(self, class_map: Dict[str, Type], kvetch_schema: Schema) -> None:

        # class map: str ==> cls
        # type id map: str => type_id
        # reverse type id map: type_id => str
        self.__class_map = class_map
        type_id_map = {obj.type_name: obj.type_id for obj in kvetch_schema.objects}
        self.__type_id_map = type_id_map
        self.__reverse_type_id_map = reverse_dict(type_id_map)

    def get_type(self, type_id: int) -> Type:
        cls_string = self.__reverse_type_id_map[type_id]
        return self.__class_map[cls_string]

    def get_type_id(self, cls: Type) -> int:
        return self.__type_id_map[cls.__name__]

    def get_class_from_name(self, name: str) -> Type:
        return self.__class_map[name]


class PentContext:
    def __init__(self, *, kvetch: Kvetch, config: PentConfig) -> None:
        self.__kvetch = kvetch
        self.__config = config
        self.loader = PentLoader(self)

    def cls_from_name(self, name: str) -> Type:
        return self.__config.get_class_from_name(name)

    @property
    def kvetch(self) -> Kvetch:
        return self.__kvetch

    @property
    def config(self) -> PentConfig:
        return self.__config

    # had to change things to nuke loader at beginning of request again
    # @property
    # def loader(self) -> 'PentLoader':
    #     return self.__loader


class PentMutationData:
    @staticmethod
    def __copy_list(seq: List[Any]) -> List[Any]:
        return [PentMutationData.__copy_obj(obj) for obj in seq]

    @staticmethod
    def __copy_obj(obj: Any) -> Any:
        return obj._asdict() if isinstance(obj, PentMutationData) else obj

    def __init__(self, data: Dict) -> None:
        self._data = data

    def _asdict(self) -> Dict:
        out = dict()
        for key, value in self._data.items():
            if value is None:
                continue
            if isinstance(value, list):
                out[key] = PentMutationData.__copy_list(value)
            else:
                out[key] = PentMutationData.__copy_obj(value)
        return out

    def _hasattr(self, attr: str) -> bool:
        return self._data.get(attr, None) is not None


class PentContextfulObject:
    def __init__(self, context: PentContext) -> None:
        self.__context = context

    @property
    def context(self) -> PentContext:
        return self.__context


# This is how self type refs are done per http://bit.ly/2szwzvL
TPent = TypeVar('TPent', bound='Pent')


class Pent(PentContextfulObject):
    def __init__(self, context: PentContext, obj_id: UUID, data: Dict) -> None:
        super().__init__(context)
        self._obj_id = obj_id
        self._data = data

    @property
    def kvetch(self) -> Kvetch:
        return self.context.kvetch

    @classmethod
    async def gen(cls: Type[TPent], context: PentContext, obj_id: UUID) -> TPent:
        """Load a pent by ID. Ensures that the return value matches the calling class
        user = await TodoUser.gen(context, obj_id)
        pent = await Pent.gen(context, obj_id)
        """
        pent = await context.loader.load(obj_id)
        if not pent:
            return None
        check.isinst(pent, cls)
        return cast(TPent, pent)

    @classmethod
    async def gen_list(cls: Type[TPent], context: PentContext, obj_ids: List[UUID]) -> List[TPent]:
        """Load a list of pents by ID. Ensures that each list member matches the calling class
        users = await TodoUser.gen_list(context, obj_ids)
        """
        pents = await context.loader.load_many(obj_ids)

        for pent in pents:
            check.isinst(pent, cls)
        return cast(List[TPent], list(pents))

    @classmethod
    async def gen_dict(cls: Type[TPent], context: PentContext,
                       ids: List[UUID]) -> Dict[UUID, TPent]:
        """Load a dictionary of pents by ID."""
        obj_list = await cls.gen_list(context, ids)
        return dict(zip([obj.obj_id for obj in obj_list], obj_list))

    @classmethod
    async def gen_browse(cls: Type[TPent], context: PentContext, after: UUID,
                         first: int) -> List[TPent]:
        """Browse and paginate over all objects of a specific type. Useful for adminstrative
        tools and debugging"""
        type_id = context.config.get_type_id(cls)
        data_list = await context.kvetch.gen_objects_of_type(type_id, after, first)
        return [cls(context, data['obj_id'], data) for data in data_list.values()]

    @classmethod
    async def gen_from_index(cls: Type[TPent], context: PentContext, index_name: str,
                             value: Any) -> TPent:
        # TODO need to filter out objects that don't match the index because of
        # temporary inconsistency issues
        obj_id = await context.kvetch.gen_id_from_index(index_name, value)
        if not obj_id:
            return None
        return await cls.gen(context, obj_id)

    @property
    def obj_id(self) -> UUID:
        return self._obj_id

    async def gen_edges_to(self, edge_name: str, after: UUID=None,
                           first: int=None) -> List[EdgeData]:
        edge_definition = self.kvetch.get_edge_definition_by_name(edge_name)
        return await self.kvetch.gen_edges(edge_definition, self._obj_id, after=after, first=first)

    async def gen_associated_pents_dynamic(
        self, cls_name: str, edge_name: str, after: UUID=None, first: int=None
    ) -> 'List[Pent]':

        cls = self.context.cls_from_name(cls_name)
        return await self.gen_associated_pents(cls, edge_name, after, first)

    async def gen_from_stored_id_dynamic(self, cls_name: str, key: str) -> 'Pent':
        obj_id = self._data.get(key)
        if not obj_id:
            return None

        cls = self.context.cls_from_name(cls_name)
        pent = await cls.gen(self.context, obj_id)
        return cast('Pent', pent)

    async def gen_associated_pents(
        self, cls: Type[TPent], edge_name: str, after: UUID=None, first: int=None
    ) -> List[TPent]:
        edges = await self.gen_edges_to(edge_name, after=after, first=first)
        to_ids = [edge.to_id for edge in edges]
        return await cls.gen_list(self.context, to_ids)


async def create_pent(context: PentContext, cls: Type[TPent],
                      mutation_data: PentMutationData) -> TPent:
    type_id = context.config.get_type_id(cls)
    new_id = await context.kvetch.gen_insert_object(type_id, mutation_data._asdict())
    return await cls.gen(context, new_id)


async def update_pent(
    context: PentContext, cls: Type[TPent], obj_id: UUID, mutation_data: PentMutationData
) -> TPent:
    data = mutation_data._asdict()
    await context.kvetch.gen_update_object(obj_id, data)
    context.loader.clear(obj_id)
    return await cls.gen(context, obj_id)


async def delete_pent(context: PentContext, _cls: Type, obj_id: UUID) -> UUID:
    value = await context.kvetch.gen_delete_object(obj_id)
    context.loader.clear(obj_id)
    return value


class PentLoader(DataLoader):
    def __init__(self, context: PentContext) -> None:
        super().__init__(batch_load_fn=self._load_pents)
        self.context = context

    async def _load_pents(self, ids: List[UUID]) -> Sequence[Pent]:
        obj_dict = await self._actual_load_pent_dict(ids)
        return list(obj_dict.values())

    async def _actual_load_pent_dict(self, ids: List[UUID]) -> Dict[UUID, Pent]:
        obj_dict = await self.context.kvetch.gen_objects(ids)
        pent_dict = OrderedDict()  # type: OrderedDict[UUID, Pent]
        for obj_id, data in obj_dict.items():
            if not data:
                pent_dict[obj_id] = None
            else:
                cls = self.context.config.get_type(data['type_id'])
                pent_dict[obj_id] = cls(self.context, obj_id, data)
        return pent_dict


def is_direct_subclass(obj: Any, subcls: Type) -> bool:
    return inspect.isclass(obj) and issubclass(obj, subcls)


def create_class_map(mod: Any) -> Dict[str, Type]:
    types = []
    for name, cls in inspect.getmembers(mod):
        if is_direct_subclass(cls, Pent):
            types.append((name, cls))
        elif is_direct_subclass(cls, PentMutationData):
            types.append((name, cls))
        elif is_direct_subclass(cls, PentMutationPayload):
            types.append((name, cls))

    return dict(types)


class PentMutationPayload:
    pass

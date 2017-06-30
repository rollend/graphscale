from graphscale import check
from graphscale.utils import to_snake_case
from .code_writer import CodeWriter
from .parser import FieldVarietal, NonNullTypeRef

GRAPPLE_PENT_HEADER = """from graphscale import check
from graphscale.grapple.graphql_impl import (
    gen_create_pent_dynamic,
    gen_delete_pent_dynamic,
    gen_update_pent_dynamic,
    gen_browse_pents_dynamic,
    gen_pent_dynamic,
)
from graphscale.pent import Pent, PentMutationData, create_pent, delete_pent, update_pent

from . import pents
"""


def print_generated_pents_file_body(document_ast):
    writer = CodeWriter()
    if document_ast.query_type():
        print_root_class(writer, document_ast, document_ast.query_type())
    if document_ast.mutation_type():
        print_root_class(writer, document_ast, document_ast.mutation_type())
    for pent_type in document_ast.pents():
        print_generated_pent(writer, document_ast, pent_type)

    for pent_mutation_data in document_ast.pent_mutation_datas():
        print_generated_pent_mutation_data(writer, document_ast, pent_mutation_data)

    writer.blank_line()
    return writer.result()


def print_generated_pents_file(document_ast):
    return GRAPPLE_PENT_HEADER + '\n' + print_generated_pents_file_body(document_ast)


def print_generated_pent_mutation_data(writer, document_ast, grapple_type):
    writer.line('class %sGenerated(PentMutationData):' % grapple_type.name)
    writer.increase_indent()  # begin class implementation

    writer.line('def __init__(self, *,')
    writer.increase_indent()  # begin arg list
    for field in grapple_type.fields:
        if field.type_ref.is_nonnull:
            writer.line('{name},'.format(name=field.python_name))
        else:
            writer.line('{name}=None,'.format(name=field.python_name))
    writer.decrease_indent()  # end arg list
    writer.line('):')
    writer.increase_indent()  # begin __init__ impl

    writer.line('self._data = locals()')
    writer.line("del self._data['self']")

    writer.decrease_indent()  # end __init__ impl
    writer.blank_line()
    print_generated_fields(writer, document_ast, grapple_type.fields)
    writer.decrease_indent()  # end class definition


def print_root_class(writer, document_ast, grapple_type):
    writer.line('class %sGenerated:' % grapple_type.name)
    writer.increase_indent()  # begin class implementation
    writer.line('@property')
    writer.line('def context(self):')
    writer.increase_indent()  # begin context impl
    writer.line("raise Exception('must implement in Root')")
    writer.decrease_indent()  # endcontext impl
    writer.blank_line()
    print_generated_fields(writer, document_ast, grapple_type.fields)
    writer.blank_line()
    writer.decrease_indent()  # end class definition


def print_generated_pent(writer, document_ast, grapple_type):
    writer.line('class %sGenerated(Pent):' % grapple_type.name)
    writer.increase_indent()  # begin class implementation
    print_generated_fields(writer, document_ast, grapple_type.fields)
    writer.decrease_indent()  # end class definition


def print_generated_fields(writer, document_ast, fields):
    wrote_once = False
    for field in fields:
        if field.field_varietal.is_custom_impl:
            continue
        elif field.field_varietal == FieldVarietal.VANILLA:
            print_vanilla_field(writer, field)
        elif field.field_varietal == FieldVarietal.READ_PENT:
            print_read_pent_field(writer, field)
        elif field.field_varietal == FieldVarietal.CREATE_PENT:
            print_create_pent_field(writer, document_ast, field)
        elif field.field_varietal == FieldVarietal.DELETE_PENT:
            print_delete_pent_field(writer, field)
        elif field.field_varietal == FieldVarietal.UPDATE_PENT:
            print_update_pent_field(writer, document_ast, field)
        elif field.field_varietal == FieldVarietal.BROWSE_PENTS:
            print_browse_pents_field(writer, field)
        elif field.field_varietal == FieldVarietal.GEN_FROM_STORED_ID:
            print_gen_from_stored_id_field(writer, field)
        elif field.field_varietal == FieldVarietal.EDGE_TO_STORED_ID:
            print_edge_to_stored_id_field(writer, field)
        else:
            raise Exception('unsupported varietal')

        wrote_once = True

    if not wrote_once:
        writer.line('pass')


def get_first_after_args(field):
    check.invariant(len(field.args) == 2, 'browse/conn should have 2 args')
    first_arg = get_required_arg(field.args, 'first')
    check.invariant(first_arg.default_value, 'must have default value')

    after_arg = get_required_arg(field.args, 'after')
    check.invariant(after_arg.type_ref.graphql_typename == 'UUID', 'arg must be UUID')

    check.invariant(field.type_ref.is_nonnull, 'outer non null')
    check.invariant(field.type_ref.inner_type.is_list, 'then list')
    check.invariant(field.type_ref.inner_type.inner_type.is_nonnull, 'then nonnull')
    check.invariant(field.type_ref.inner_type.inner_type.inner_type.is_named, 'then named')
    target_type = field.type_ref.inner_type.inner_type.inner_type.python_typename
    return (first_arg, after_arg, target_type)


def print_browse_pents_field(writer, field):
    first_arg, after_arg, browse_type = get_first_after_args(field)

    writer.line('async def %s(self, first, after=None):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await gen_browse_pents_dynamic(self.context, after, first, '%s')" % browse_type
    )
    writer.decrease_indent()  # end implementation
    writer.blank_line()


def print_update_pent_field(writer, document_ast, field):
    check.invariant(len(field.args) == 2, 'updatePent should have 2 args')
    check_required_id_arg(field)
    pent_cls, data_cls, payload_cls = get_mutation_classes(document_ast, field)

    writer.line('async def %s(self, obj_id, data):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await gen_update_pent_dynamic"
        "(self.context, obj_id, '{pent_cls}', '{data_cls}', '{payload_cls}', data)".format(
            pent_cls=pent_cls, data_cls=data_cls, payload_cls=payload_cls
        )
    )
    writer.decrease_indent()  # end implementation
    writer.blank_line()


def print_delete_pent_field(writer, field):
    check.invariant(len(field.args) == 1, 'deletePent should only have 1 arg')
    check_required_id_arg(field)
    payload_cls = field.type_ref.python_typename
    pent_cls = field.field_varietal_data.type

    writer.line('async def %s(self, obj_id):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await gen_delete_pent_dynamic(self.context, '{pent_cls}', '{payload_cls}', obj_id)".
        format(pent_cls=pent_cls, payload_cls=payload_cls)
    )
    writer.decrease_indent()  # end implementation
    writer.blank_line()


def get_mutation_classes(document_ast, field):
    data_cls = get_data_arg_in_pent(field)
    payload_cls = field.type_ref.python_typename

    payload_type = document_ast.type_named(payload_cls)
    check.invariant(
        len(payload_type.fields) == 1, 'payload class for vanilla crud should only have one field'
    )
    data_field = payload_type.fields[0]
    pent_cls = data_field.type_ref.python_typename
    return (pent_cls, data_cls, payload_cls)


def print_create_pent_field(writer, document_ast, field):
    check.invariant(len(field.args) == 1, 'createPent should only have 1 arg')

    pent_cls, data_cls, payload_cls = get_mutation_classes(document_ast, field)

    writer.line('async def %s(self, data):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await gen_create_pent_dynamic"
        "(self.context, '{pent_cls}', '{data_cls}', '{payload_cls}', data)".format(
            pent_cls=pent_cls, data_cls=data_cls, payload_cls=payload_cls
        )
    )
    writer.decrease_indent()  # end implemenation
    writer.blank_line()


def print_read_pent_field(writer, field):
    writer.line('async def %s(self, obj_id):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await gen_pent_dynamic(self.context, '%s', obj_id)" % field.type_ref.python_typename
    )
    writer.decrease_indent()  # end implemenation
    writer.blank_line()


def print_vanilla_field(writer, field):
    writer.line('@property')
    writer.line('def %s(self):' % field.python_name)
    writer.increase_indent()  # begin property implemenation
    if not field.type_ref.is_nonnull:
        writer.line("return self._data.get('%s')" % field.python_name)
    else:
        writer.line("return self._data['%s']" % field.python_name)
    writer.decrease_indent()  # end property definition
    writer.blank_line()


def get_required_arg(args, name):
    for arg in args:
        if arg.name == name:
            return arg

    check.failed('arg with name %s could not be found' % name)


def get_data_arg_in_pent(field):
    data_arg = get_required_arg(field.args, 'data')
    check.invariant(
        isinstance(data_arg.type_ref, NonNullTypeRef), 'input argument must be non null'
    )

    return data_arg.type_ref.inner_type.python_typename


def check_required_id_arg(field):
    id_arg = get_required_arg(field.args, 'id')
    check.invariant(id_arg.type_ref.is_nonnull, 'arg must be non null')
    check.invariant(id_arg.type_ref.inner_type.graphql_typename == 'UUID', 'arg must be UUID')


def print_edge_to_stored_id_field(writer, field):
    first_arg, after_arg, target_type = get_first_after_args(field)
    writer.line('async def %s(self, first, after=None):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await self.gen_associated_pents_dynamic"
        "('{target_type}', '{edge_name}', after, first)".format(
            target_type=target_type, edge_name=field.field_varietal_data.edge_name
        )
    )
    writer.decrease_indent()  # end implementation
    writer.blank_line()


def print_gen_from_stored_id_field(writer, field):
    check.invariant(len(field.args) == 0, 'genFromStoredId should have no args')
    check.invariant(field.type_ref.is_named, 'only supports bare types for now')

    cls_name = field.type_ref.python_typename
    # very hard coded for now. should be configurable via argument to directive optionally
    prop = to_snake_case(field.name) + '_id'

    writer.line('async def %s(self):' % field.python_name)
    writer.increase_indent()  # begin implemenation
    writer.line(
        "return await self.gen_from_stored_id_dynamic"
        "('{cls_name}', '{prop}')".format(cls_name=cls_name, prop=prop)
    )
    writer.decrease_indent()  # end implementation
    writer.blank_line()

#
# spyne - Copyright (C) Spyne contributors.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
#

import logging
logger = logging.getLogger(__name__)

import shutil
import tempfile

import spyne.const.xml_ns

from lxml import etree

from spyne.util.cdict import cdict
from spyne.util.odict import odict

from spyne.model import SimpleModel
from spyne.model import ByteArray
from spyne.model import ComplexModelBase
from spyne.model import Fault
from spyne.model import Decimal
from spyne.model import DateTime
from spyne.model import Date
from spyne.model import Time
from spyne.model import Unicode

from spyne.model.enum import EnumBase
from spyne.interface import InterfaceDocumentBase

from spyne.interface.xml_schema.model import byte_array_add
from spyne.interface.xml_schema.model import simple_add
from spyne.interface.xml_schema.model import complex_add
from spyne.interface.xml_schema.model import fault_add
from spyne.interface.xml_schema.model import enum_add

from spyne.interface.xml_schema.model import simple_get_restriction_tag
from spyne.interface.xml_schema.model import unicode_get_restriction_tag
from spyne.interface.xml_schema.model import Tget_range_restriction_tag


_add_handlers = cdict({
    object: lambda interface, cls, tags: None,
    ByteArray: byte_array_add,
    SimpleModel: simple_add,
    ComplexModelBase: complex_add,
    Fault: fault_add,
    EnumBase: enum_add,
})

_get_restriction_tag_handlers = cdict({
    object: lambda self, cls: None,
    SimpleModel: simple_get_restriction_tag,
    Unicode: unicode_get_restriction_tag,
    Decimal: Tget_range_restriction_tag(Decimal),
    DateTime: Tget_range_restriction_tag(DateTime),
    Time: Tget_range_restriction_tag(Time),
    Date: Tget_range_restriction_tag(Date),
})

_ns_xsd = spyne.const.xml_ns.xsd
_ns_wsa = spyne.const.xml_ns.wsa
_ns_wsdl = spyne.const.xml_ns.wsdl
_ns_soap = spyne.const.xml_ns.soap
_pref_wsa = spyne.const.xml_ns.const_prefmap[_ns_wsa]


class SchemaInfo(object):
    def __init__(self):
        self.elements = odict()
        self.types = odict()


class XmlSchema(InterfaceDocumentBase):
    """The implementation of a subset of the Xml Schema 1.0 object definition
    document standard.

    The standard is available in three parts as follows:
    http://www.w3.org/TR/xmlschema-0/
    http://www.w3.org/TR/xmlschema-1/
    http://www.w3.org/TR/xmlschema-2/

    :param interface: A :class:`spyne.interface.InterfaceBase` instance.

    Supported events:
        * document_built:
            Called right after the document is built. The handler gets the
            ``XmlSchema`` instance as the only argument.

        * xml_document_built:
            Called right after the document is built. The handler gets the
            ``XmlSchema`` instance as the only argument. Only called from this
            class.
    """

    def __init__(self, interface):
        InterfaceDocumentBase.__init__(self, interface)

        self.schema_dict = {}
        self.validation_schema = None

        pref = self.interface.prefmap[self.interface.app.tns]
        self.namespaces = odict({pref: SchemaInfo()})

        self.complex_types = set()

    def add(self, cls, tags):
        if not (cls in tags):
            tags.add(cls)

            handler = _add_handlers[cls]
            handler(self, cls, tags)

    def get_restriction_tag(self, cls):
        handler = _get_restriction_tag_handlers[cls]
        return handler(self, cls)

    def build_schema_nodes(self, with_schema_location=False):
        self.schema_dict = {}

        for cls in self.interface.classes.values():
            self.add(cls, set())

        for pref in self.namespaces:
            schema = self.get_schema_node(pref)

            # append import tags
            for namespace in self.interface.imports[self.interface.nsmap[pref]]:
                import_ = etree.SubElement(schema, "{%s}import" % _ns_xsd)

                import_.set("namespace", namespace)
                import_pref = self.interface.get_namespace_prefix(namespace)
                if with_schema_location and \
                                        self.namespaces.get(import_pref, False):
                    import_.set('schemaLocation', "%s.xsd" % import_pref)

                sl = spyne.const.xml_ns.schema_location.get(namespace, None)
                if not (sl is None):
                    import_.set('schemaLocation', sl)

            # append simpleType and complexType tags
            for node in reversed(self.namespaces[pref].types.values()):
                schema.append(node)

            # append element tags
            for node in self.namespaces[pref].elements.values():
                schema.append(node)

        self.add_missing_elements_for_methods()

        self.event_manager.fire_event('document_built', self)
        self.event_manager.fire_event('xml_document_built', self)

    def add_missing_elements_for_methods(self):
        def missing_methods():
            for service in self.interface.services:
                for method in service.public_methods.values():
                    yield method

        pref_tns = self.interface.prefmap[self.interface.tns]

        elements = self.get_schema_info(pref_tns).elements
        schema_root = self.schema_dict[pref_tns]
        for method in missing_methods():
            name = method.in_message.Attributes.sub_name
            if name is None:
                name = method.in_message.get_type_name()

            if not name in elements:
                element = etree.Element('{%s}element' % _ns_xsd)
                element.set('name', name)
                element.set('type', method.in_message.get_type_name_ns(
                                                                self.interface))
                elements[name] = element
                schema_root.append(element)

            if method.out_message is not None:
                name = method.out_message.Attributes.sub_name
                if name is None:
                    name = method.out_message.get_type_name()
                if not name in elements:
                    element = etree.Element('{%s}element' % _ns_xsd)
                    element.set('name', name)
                    element.set('type', method.out_message \
                                              .get_type_name_ns(self.interface))
                    elements[name] = element
                    schema_root.append(element)

    def build_validation_schema(self):
        """Build application schema specifically for xml validation purposes."""

        self.build_schema_nodes(with_schema_location=True)

        pref_tns = self.interface.get_namespace_prefix(self.interface.tns)
        tmp_dir_name = tempfile.mkdtemp(prefix='spyne')
        logger.debug("generating schema for targetNamespace=%r, prefix: "
                  "%r in dir %r" % (self.interface.tns, pref_tns, tmp_dir_name))

        try:
            # serialize nodes to files
            for k, v in self.schema_dict.items():
                file_name = '%s/%s.xsd' % (tmp_dir_name, k)
                with open(file_name, 'wb') as f:
                    etree.ElementTree(v).write(f, pretty_print=True)

                logger.debug("writing %r for ns %s" %
                             (file_name, self.interface.nsmap[k]))

            logger.debug("building schema...")
            with open('%s/%s.xsd' % (tmp_dir_name, pref_tns), 'r') as f:
                try:
                    self.validation_schema = etree.XMLSchema(etree.parse(f))
                except Exception:
                    f.seek(0)
                    logger.error(etree.tostring(etree.parse(f),
                                                pretty_print=True))
                    logger.error("This is a Spyne error. Please seek support "
                                 "with a minimal test case that reproduces "
                                 "this error.")
                    raise

            logger.debug("schema %r built" % self.validation_schema)
        finally:
            logger.debug("cleaning up ...")
            shutil.rmtree(tmp_dir_name)
            logger.debug("removed %r" % tmp_dir_name)

    def get_schema_node(self, pref):
        """Return schema node for the given namespace prefix."""

        if not (pref in self.schema_dict):
            schema = etree.Element("{%s}schema" % _ns_xsd,
                                                     nsmap=self.interface.nsmap)

            schema.set("targetNamespace", self.interface.nsmap[pref])
            schema.set("elementFormDefault", "qualified")

            self.schema_dict[pref] = schema

        else:
            schema = self.schema_dict[pref]

        return schema

    def get_interface_document(self):
        return self.schema_dict

    def build_interface_document(self):
        self.build_schema_nodes()

    def add_element(self, cls, node):
        pref = cls.get_element_name_ns(self.interface).split(":")[0]

        schema_info = self.get_schema_info(pref)
        name = cls.Attributes.sub_name or cls.get_type_name()
        schema_info.elements[name] = node

    def add_simple_type(self, cls, node):
        tn = cls.get_type_name()
        pref = cls.get_namespace_prefix(self.interface)

        schema_info = self.get_schema_info(pref)
        schema_info.types[tn] = node

    def add_complex_type(self, cls, node):
        tn = cls.get_type_name()
        pref = cls.get_namespace_prefix(self.interface)

        schema_info = self.get_schema_info(pref)
        schema_info.types[tn] = node

    def get_schema_info(self, prefix):
        """Returns the SchemaInfo object for the corresponding namespace. It
        creates it if it doesn't exist.

        The SchemaInfo object holds the simple and complex type definitions
        for a given namespace."""

        if prefix in self.namespaces:
            schema = self.namespaces[prefix]
        else:
            schema = self.namespaces[prefix] = SchemaInfo()

        return schema

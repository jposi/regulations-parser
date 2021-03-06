#vim: set encoding=utf-8
import itertools
import logging
import re
import string

from regparser.citations import Label, remove_citation_overlaps
from regparser.tree.interpretation import merge_labels, text_to_labels
from regparser.tree.struct import Node, treeify
from regparser.tree.xml_parser import tree_utils
from regparser.utils import roman_nums


i_levels = [
    [str(i) for i in range(1, 51)],
    list(itertools.islice(roman_nums(), 0, 50)),
    list(string.ascii_uppercase),
    # We don't include the closing tag - it won't be closed if followed by a
    # key term
    ['<E T="03">' + str(i) for i in range(1, 51)],
]


_marker_regex = re.compile(
    r'^\s*('                   # line start
    + '([0-9]+)'               # digits
    + '|([ivxlcdm]+)'          # roman
    + '|([A-Z]+)'              # upper
    + '|(<E[^>]*>[0-9]+)'      # emphasized digit
    + r')\s*\..*', re.DOTALL)  # followed by a period and then anything


_marker_stars_regex = re.compile(
    r'^\s*('                   # line start
    + '([0-9]+)'               # digits
    + '|([ivxlcdm]+)'          # roman
    + '|([A-Z]+)'              # upper
    + '|(<E[^>]*>[0-9]+)'      # emphasized digit
    + r')\s+\* \* \*\s*$', re.DOTALL)  # followed by stars


def get_first_interp_marker(text):
    match = _marker_regex.match(text)
    if match:
        return text[:text.find('.')].strip()        # up to dot
    match = _marker_stars_regex.match(text)
    if match:
        return text[:text.find('*')].strip()        # up to star


def interpretation_level(marker, previous_level=None):
    """
        Based on the marker, determine the interpretation paragraph level.
        Levels 1,2 don't need this, since they are marked differently.
        Frustratingly, the XML is not always marked up correctly - some
        markers are sometimes italicized when they shouldn't be.
    """
    #   First, non-italics
    for idx, lst in enumerate(i_levels[:3]):
        if marker in lst:
            return idx + 3
    #   Italics don't always mean what we'd like (le sigh)
    for idx, lst in enumerate(i_levels[3:]):
        idx = idx + 3   # Shift
        if marker in lst:
            #   Probably meant non-italic...
            if previous_level is not None and idx + 3 > previous_level + 1:
                return idx
            else:
                return idx + 3


_first_markers = [re.compile(ur'[\.|,|;|\-|—]\s*(' + marker + ')\.')
                  for marker in ['i', 'A']]


def collapsed_markers_matches(node_text):
    """Find collapsed markers, i.e. tree node paragraphs that begin within a
    single XML node, within this text. Remove citations and other false
    positives"""
    collapsed_markers = []
    for marker in _first_markers:
        possible = ((m, m.start(), m.end())
                    for m in marker.finditer(node_text) if m.start() > 0)
        possible = remove_citation_overlaps(node_text, possible)
        # If certain characters follow, kill it
        for following in ("e.", ")", u"”", '"', "'"):
            possible = [(m, s, end) for m, s, end in possible
                        if not node_text[end:].startswith(following)]
        collapsed_markers.extend(m for m, _, _ in possible)
    return collapsed_markers


def interp_inner_child(child_node, stack):
    """ Build an inner child node (basically a node that's after
    -Interp- in the tree). If the paragraph doesn't have a marker, attach it
    to the previous paragraph"""
    node_text = tree_utils.get_node_text(child_node, add_spaces=True)
    text_with_tags = tree_utils.get_node_text_tags_preserved(child_node)
    first_marker = get_first_interp_marker(text_with_tags)
    if not first_marker and stack.lineage():
        logging.warning("Couldn't determine interp marker. Appending to "
                        "previous paragraph: %s", node_text)
        previous = stack.lineage()[0]
        previous.text += "\n\n" + node_text
        if hasattr(previous, 'tagged_text'):
            previous.tagged_text += "\n\n" + text_with_tags
        else:
            previous.tagged_text = text_with_tags
    else:
        child_with_marker(child_node, stack)


def child_with_marker(child_node, stack):
    """Machinery to build a node for an interp's inner child. Assumes the
    paragraph begins with a paragraph marker."""
    node_text = tree_utils.get_node_text(child_node, add_spaces=True)
    text_with_tags = tree_utils.get_node_text_tags_preserved(child_node)
    first_marker = get_first_interp_marker(text_with_tags)

    collapsed = collapsed_markers_matches(node_text)

    #   -2 throughout to account for matching the character + period
    ends = [m.end() - 2 for m in collapsed[1:]] + [len(node_text)]
    starts = [m.end() - 2 for m in collapsed] + [len(node_text)]

    #   Node for this paragraph
    n = Node(node_text[0:starts[0]], label=[first_marker],
             node_type=Node.INTERP)
    n.tagged_text = text_with_tags
    last = stack.peek()

    if len(last) == 0:
        stack.push_last((interpretation_level(first_marker), n))
    else:
        node_level = interpretation_level(first_marker, last[0][0])
        if node_level is None:
            logging.warning("Couldn't determine node_level for this "
                            + "interpretation paragraph: " + n.text)
            node_level = last[0][0] + 1
        stack.add(node_level, n)

    #   Collapsed-marker children
    for match, end in zip(collapsed, ends):
        n = Node(node_text[match.end() - 2:end], label=[match.group(1)],
                 node_type=Node.INTERP)
        node_level = interpretation_level(match.group(1))
        last = stack.peek()
        if len(last) == 0:
            stack.push_last((node_level, n))
        else:
            stack.add(node_level, n)


def is_title(xml_node):
    """Not all titles are created equal. Sometimes a title appears as a
    paragraph tag, mostly to add confusion."""
    if xml_node.getchildren():
        child = xml_node.getchildren()[0]
    else:
        child = None
    return bool(
        (xml_node.tag.upper() == 'HD' and xml_node.attrib['SOURCE'] != 'HED')
        or (xml_node.tag.upper() == 'P'
            and (xml_node.text is None or not xml_node.text.strip())
            and len(xml_node.getchildren()) == 1
            and (child.tail is None or not child.tail.strip(" \n\t."))
            and text_to_labels(child.text, Label(), warn=False))
        or (xml_node.tag.upper() == 'P'
            and len(xml_node.getchildren()) == 0
            and xml_node.text and not get_first_interp_marker(xml_node.text)
            and text_to_labels(xml_node.text, Label(), warn=False,
                               force_start=True)))


def process_inner_children(inner_stack, xml_node):
    """Process the following nodes as children of this interpretation"""
    children = itertools.takewhile(
        lambda x: not is_title(x), xml_node.itersiblings())
    for c in filter(lambda c: c.tag == 'P', children):
        interp_inner_child(c, inner_stack)


def missing_levels(last_label, label):
    """Sometimes we will have a 2(a)(1) without seeing 2(a). Fill in the
    missing level"""
    #   Only care about data before 'Interp'
    label = list(itertools.takewhile(lambda l: l != Node.INTERP_MARK, label))
    #   Find only the shared segments
    zipped = zip(last_label, label)
    shared = list(itertools.takewhile(lambda pair: pair[0] == pair[1], zipped))

    missing = []
    #   Add layers in between, but do not add the last; e.g. add 2(a) but
    #   not 2(a)(1)
    for i in range(len(shared) + 1, len(label)):
        level_label = label[:i] + [Node.INTERP_MARK]
        missing.append(Node(node_type=Node.INTERP, label=level_label))
    return missing


def parse_from_xml(root, xml_nodes):
    """Core of supplement processing; shared by whole XML parsing and notice
    parsing. root is the root interpretation node (e.g. a Node with label
    '1005-Interp'). xml_nodes contains all XML nodes which will be relevant
    to the interpretations"""
    supplement_nodes = [root]

    last_label = root.label
    header_count = 0
    for ch in xml_nodes:
        node = Node(label=last_label, node_type=Node.INTERP)
        label_obj = Label.from_node(node)

        #   Explicitly ignore "subpart" headers, as they are inconsistent
        #   and they will be reconstructed as subterps client-side
        text = tree_utils.get_node_text(ch, add_spaces=True)
        if is_title(ch) and 'subpart' not in text.lower():
            labels = text_to_labels(text, label_obj)
            if labels:
                label = merge_labels(labels)
            else:   # Header without a label, like an Introduction, etc.
                header_count += 1
                label = root.label[:2] + ['h%d' % header_count]

            inner_stack = tree_utils.NodeStack()
            missing = missing_levels(last_label, label)
            supplement_nodes.extend(missing)
            last_label = label

            node = Node(node_type=Node.INTERP, label=label,
                        title=text.strip())
            inner_stack.add(2, node)

            process_inner_children(inner_stack, ch)

            while inner_stack.size() > 1:
                inner_stack.unwind()

            ch_node = inner_stack.m_stack[0][0][1]
            supplement_nodes.append(ch_node)

    supplement_tree = treeify(supplement_nodes)

    def per_node(node):
        node.label = [l.replace('<E T="03">', '') for l in node.label]
        for child in node.children:
            per_node(child)
    for node in supplement_tree:
        per_node(node)

    return supplement_tree[0]


def build_supplement_tree(reg_part, node):
    """ Build the tree for the supplement section. """
    title = get_app_title(node)
    root = Node(
        node_type=Node.INTERP,
        label=[reg_part, Node.INTERP_MARK],
        title=title)

    return parse_from_xml(root, node.getchildren())


def get_app_title(node):
    """ Appendix/Supplement sections have the title in an HD tag, or
    if they are reserved, in a <RESERVED> tag. Extract the title. """

    titles = node.xpath("./HD[@SOURCE='HED']")
    if titles:
        return titles[0].text
    else:
        return node.xpath("./RESERVED")[0]

import QtQuick

Column {
    id: root
    property alias title: heading.text
    property bool sectionVisible: true
    default property alias content: inner.data
    width: parent ? parent.width : 0
    spacing: 8
    visible: sectionVisible
    height: visible ? implicitHeight : 0

    SectionLabel {
        id: heading
        width: parent.width
        topPadding: 8
    }
    Card {
        width: parent.width
        implicitHeight: inner.implicitHeight + 24
        Column {
            id: inner
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 10
        }
    }
}

import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property string raw: ""
    property string kind: "domain"
    property string label: root.raw
    property bool group: false
    signal removeRequested()
    signal clicked()

    readonly property color mark: {
        if (root.group)
            return T.accent
        if (root.kind === "process" || root.kind === "service")
            return T.warn
        if (root.kind === "ip")
            return "#8b95a8"
        return T.accent
    }

    implicitWidth: row.implicitWidth + 16
    implicitHeight: 26
    radius: 8
    color: Qt.rgba(1, 1, 1, 0.04)
    border.width: 1
    border.color: Qt.rgba(1, 1, 1, 0.06)

    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: 8
        spacing: 6

        Rectangle {
            width: 6
            height: 6
            radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: root.mark
        }
        Text {
            text: root.group ? root.label : root.label
            color: T.text
            font.pixelSize: 11
            font.family: T.fontUi
            elide: Text.ElideMiddle
            width: Math.min(implicitWidth, 220)
        }
        Text {
            visible: !root.group
            text: "×"
            color: T.muted
            font.pixelSize: 13
            font.family: T.fontUi
            MouseArea {
                anchors.fill: parent
                anchors.margins: -6
                cursorShape: Qt.PointingHandCursor
                onClicked: root.removeRequested()
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: root.clicked()
        enabled: root.group
        cursorShape: root.group ? Qt.PointingHandCursor : Qt.ArrowCursor
    }
}

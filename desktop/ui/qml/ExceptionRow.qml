import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property string raw: ""
    property string kind: "domain"
    property string label: root.raw
    signal removeRequested()

    readonly property string kindTitle: {
        if (root.kind === "process")
            return "Программа"
        if (root.kind === "service")
            return "Служба"
        if (root.kind === "ip")
            return "IP"
        return "Домен"
    }

    width: parent ? parent.width : 280
    implicitHeight: 44
    height: 44
    radius: 10
    color: Qt.rgba(1, 1, 1, 0.03)
    border.width: 1
    border.color: Qt.rgba(1, 1, 1, 0.05)

    Text {
        id: kindLbl
        anchors.left: parent.left
        anchors.leftMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        width: 78
        text: root.kindTitle
        color: T.muted
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.family: T.fontUi
        elide: Text.ElideRight
    }

    Text {
        anchors.left: kindLbl.right
        anchors.leftMargin: 6
        anchors.right: delBtn.left
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        color: T.text
        font.pixelSize: 13
        font.family: T.fontUi
        elide: Text.ElideMiddle
    }

    Rectangle {
        id: delBtn
        anchors.right: parent.right
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        width: 78
        height: 32
        radius: 8
        color: delMouse.containsMouse ? T.danger : Qt.rgba(0.941, 0.443, 0.471, 0.16)
        Text {
            anchors.centerIn: parent
            text: "Удалить"
            color: delMouse.containsMouse ? "#04140f" : T.danger
            font.pixelSize: 12
            font.weight: Font.DemiBold
            font.family: T.fontUi
        }
        MouseArea {
            id: delMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.removeRequested()
        }
    }
}

import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property string kind: "min"
    signal clicked()

    width: 30
    height: 30
    radius: 9
    color: {
        if (!mouse.containsMouse)
            return "transparent"
        return kind === "close" ? T.danger : T.btn
    }
    Behavior on color { ColorAnimation { duration: 110 } }

    Item {
        anchors.centerIn: parent
        width: 12
        height: 12

        Rectangle {
            visible: root.kind === "min"
            width: 10
            height: 1.5
            radius: 0.75
            anchors.centerIn: parent
            antialiasing: true
            color: mouse.containsMouse ? T.text : T.muted
        }

        Repeater {
            model: root.kind === "close" ? 2 : 0
            Rectangle {
                required property int index
                width: 11
                height: 1.5
                radius: 0.75
                anchors.centerIn: parent
                rotation: index === 0 ? 45 : -45
                antialiasing: true
                color: mouse.containsMouse ? T.text : T.muted
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}

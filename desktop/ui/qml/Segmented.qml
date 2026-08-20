import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property var model: []
    property string value: ""
    signal activated(string value)
    implicitHeight: 40
    radius: 10
    color: T.surface2
    clip: true

    Row {
        anchors.fill: parent
        anchors.margins: 3
        spacing: 3

        Repeater {
            model: root.model
            delegate: Rectangle {
                required property var modelData
                required property int index
                width: (root.width - 6 - (root.model.length - 1) * 3) / root.model.length
                height: parent.height
                radius: 8
                color: root.value === modelData.value ? T.select : "transparent"
                Behavior on color { ColorAnimation { duration: 140 } }

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: root.value === modelData.value ? T.text : T.muted
                    font.pixelSize: 12
                    font.bold: true
                    font.family: T.fontUi
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.activated(modelData.value)
                }
            }
        }
    }
}

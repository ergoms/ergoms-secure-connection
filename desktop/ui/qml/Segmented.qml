import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property var model: []
    property string value: ""
    signal activated(string value)
    implicitHeight: 36
    radius: 10
    color: T.surface2
    clip: true
    opacity: enabled ? 1 : 0.45
    border.width: 1
    border.color: Qt.rgba(1, 1, 1, 0.04)

    Row {
        anchors.fill: parent
        anchors.margins: 3
        spacing: 3

        Repeater {
            model: root.model
            delegate: Rectangle {
                required property var modelData
                required property int index
                readonly property bool selected: root.value === modelData.value
                width: (root.width - 6 - (root.model.length - 1) * 3) / root.model.length
                height: parent.height
                radius: 8
                color: selected ? Qt.rgba(0.176, 0.831, 0.659, 0.16) : "transparent"
                Behavior on color { ColorAnimation { duration: 140 } }

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: parent.selected ? T.accent : T.muted
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.2
                    font.family: T.fontUi
                }

                MouseArea {
                    anchors.fill: parent
                    enabled: root.enabled
                    cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.activated(modelData.value)
                }
            }
        }
    }
}

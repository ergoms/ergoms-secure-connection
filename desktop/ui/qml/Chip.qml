import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property string label: ""
    property bool lit: false
    implicitHeight: 26
    implicitWidth: chipText.implicitWidth + 16
    radius: 13
    color: lit ? Qt.rgba(0.176, 0.831, 0.659, 0.16) : T.surface2
    border.width: 1
    border.color: lit ? T.accent : T.border

    Text {
        id: chipText
        anchors.centerIn: parent
        text: root.label
        color: root.lit ? T.accent : T.muted
        font.pixelSize: 10
        font.bold: true
        font.family: T.fontUi
    }
}

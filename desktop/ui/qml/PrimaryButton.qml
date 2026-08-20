import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    property bool primary: false
    property bool danger: false
    property string text: ""
    signal clicked()

    radius: 14
    implicitHeight: 48
    readonly property color baseColor: danger ? T.danger : (primary ? T.accent : T.btn)
    readonly property color hoverColor: danger ? T.dangerDim : (primary ? T.accentDim : T.btnHover)
    color: mouse.containsMouse && enabled ? hoverColor : baseColor
    border.width: primary || danger ? 0 : 1
    border.color: T.border
    scale: mouse.pressed && enabled ? 0.98 : 1.0
    opacity: enabled ? 1 : 0.45
    Behavior on color { ColorAnimation { duration: 140 } }
    Behavior on scale { NumberAnimation { duration: 90 } }

    Text {
        anchors.centerIn: parent
        text: root.text
        color: root.primary && !root.danger ? "#04140f" : T.text
        font.pixelSize: 14
        font.bold: true
        font.family: T.fontUi
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.enabled
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        hoverEnabled: true
        onClicked: root.clicked()
    }
}

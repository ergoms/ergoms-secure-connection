import QtQuick

Rectangle {
    id: root
    property bool primary: false
    property bool danger: false
    property string text: ""
    signal clicked()

    radius: 12
    implicitHeight: 44
    readonly property color baseColor: danger ? T.danger : (primary ? T.accent : T.surface2)
    readonly property color hoverColor: danger ? T.dangerDim : (primary ? T.accentDim : T.btn)
    color: mouse.containsMouse && enabled ? hoverColor : baseColor
    border.width: primary || danger ? 0 : 1
    border.color: mouse.containsMouse && enabled ? T.border : T.hairline
    scale: mouse.pressed && enabled ? 0.985 : 1.0
    opacity: enabled ? 1 : 0.42
    Behavior on color { ColorAnimation { duration: 140 } }
    Behavior on scale { NumberAnimation { duration: 90 } }

    Text {
        anchors.centerIn: parent
        text: root.text
        color: root.primary && !root.danger ? T.accentText : T.text
        font.pixelSize: 13
        font.weight: Font.DemiBold
        font.letterSpacing: 0.25
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

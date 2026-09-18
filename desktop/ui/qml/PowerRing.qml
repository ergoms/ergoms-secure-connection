import QtQuick

Item {
    id: root
    property color ringColor: T.muted
    property bool busy: false
    property bool active: false
    signal clicked()
    width: 168
    height: 168

    Rectangle {
        id: glow
        anchors.centerIn: parent
        width: 148
        height: 148
        radius: 74
        color: "transparent"
        border.width: 18
        border.color: Qt.rgba(root.ringColor.r, root.ringColor.g, root.ringColor.b, T.light ? 0.10 : 0.16)
        scale: root.active || root.busy ? 1.06 : 1.0
        Behavior on scale { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
        Behavior on border.color { ColorAnimation { duration: 280 } }
    }

    Rectangle {
        anchors.centerIn: parent
        width: 118
        height: 118
        radius: 59
        color: T.surface
        border.width: 3
        border.color: Qt.rgba(root.ringColor.r, root.ringColor.g, root.ringColor.b, T.light ? 0.22 : 0.35)
        Behavior on border.color { ColorAnimation { duration: 280 } }
    }

    Canvas {
        id: arc
        anchors.fill: parent
        property real spin: 0
        onSpinChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2
            var cy = height / 2
            var r = 62
            ctx.lineWidth = 7
            ctx.lineCap = "round"
            ctx.strokeStyle = root.ringColor
            ctx.beginPath()
            var start = root.busy ? spin : -Math.PI / 2
            var span = root.busy ? Math.PI * 1.25 : Math.PI * 1.72
            ctx.arc(cx, cy, r, start, start + span, false)
            ctx.stroke()
        }
        NumberAnimation on spin {
            running: root.busy
            from: 0
            to: Math.PI * 2
            duration: 1100
            loops: Animation.Infinite
            easing.type: Easing.Linear
        }
        Connections {
            target: root
            function onRingColorChanged() { arc.requestPaint() }
            function onBusyChanged() { arc.requestPaint() }
        }
    }

    Text {
        anchors.centerIn: parent
        text: root.busy ? "…" : (root.active ? "●" : "○")
        color: root.ringColor
        font.pixelSize: 28
        font.family: T.fontUi
        Behavior on color { ColorAnimation { duration: 280 } }
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}

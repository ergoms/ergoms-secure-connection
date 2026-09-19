import QtQuick

Item {
    id: root
    property color color: T.accent
    implicitWidth: 18
    implicitHeight: 18
    width: implicitWidth
    height: implicitHeight

    Canvas {
        id: ring
        anchors.fill: parent
        property real spin: 0
        onSpinChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2
            var cy = height / 2
            var r = Math.min(cx, cy) - 2
            if (r <= 0)
                return
            ctx.lineWidth = 2.2
            ctx.lineCap = "round"
            ctx.strokeStyle = root.color
            ctx.beginPath()
            ctx.arc(cx, cy, r, spin, spin + Math.PI * 1.35, false)
            ctx.stroke()
        }
        NumberAnimation on spin {
            running: root.visible
            from: 0
            to: Math.PI * 2
            duration: 900
            loops: Animation.Infinite
            easing.type: Easing.Linear
        }
        Connections {
            target: root
            function onColorChanged() { ring.requestPaint() }
            function onVisibleChanged() { ring.requestPaint() }
        }
    }
}

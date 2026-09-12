import QtQuick
import "Theme.js" as T

Item {
    id: root
    property string message: ""
    property string kind: "info"
    property bool shown: false

    function show(msg, k) {
        message = msg
        kind = k || "info"
        shown = true
        hideTimer.restart()
    }

    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.leftMargin: 14
    anchors.rightMargin: 14
    anchors.topMargin: 60
    height: shown ? box.height : 0
    visible: shown
    z: 20

    Timer {
        id: hideTimer
        interval: 4200
        onTriggered: shown = false
    }

    Rectangle {
        id: box
        width: parent.width
        height: Math.max(44, toastText.implicitHeight + 20)
        radius: 12
        color: kind === "error" ? "#3a1d24" : (kind === "warn" ? "#3a3220" : T.surface2)
        border.width: 1
        border.color: kind === "error" ? T.danger : (kind === "warn" ? T.warn : T.accent)

        Text {
            id: toastText
            anchors.fill: parent
            anchors.margins: 10
            wrapMode: Text.WordWrap
            text: root.message
            color: T.text
            font.pixelSize: 12
            font.family: T.fontUi
            verticalAlignment: Text.AlignVCenter
        }

        MouseArea {
            anchors.fill: parent
            onClicked: root.shown = false
        }
    }
}

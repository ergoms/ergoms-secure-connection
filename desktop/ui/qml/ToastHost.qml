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

    // Compact banner under the title, left side — does not cover log Copy
    // or the settings footer.
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.leftMargin: 14
    anchors.topMargin: 58
    width: Math.min(parent.width - 130, 268)
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
        height: Math.max(40, toastText.implicitHeight + 18)
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

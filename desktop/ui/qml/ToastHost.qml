import QtQuick
import QtQuick.Layouts

Item {
    id: root
    property string message: ""
    property string kind: "info"
    property bool shown: false
    property bool overlay: true

    function show(msg, k) {
        message = msg
        kind = k || "info"
        shown = true
        hideTimer.restart()
    }

    // Overlay: unused in the main window (toasts sit in the layout above the nav).
    // Inline: journal page, or the shared strip above «Главная / Настройки / …».
    anchors.left: overlay ? parent.left : undefined
    anchors.right: overlay ? parent.right : undefined
    anchors.top: overlay ? parent.top : undefined
    anchors.leftMargin: overlay ? 14 : 0
    anchors.rightMargin: overlay ? 14 : 0
    anchors.topMargin: overlay ? 58 : 0
    Layout.fillWidth: overlay ? false : true
    height: shown ? box.height : 0
    visible: shown && bridge.page !== "log"
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
        color: kind === "error" ? T.toastError : (kind === "warn" ? T.toastWarn : T.surface2)
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

import QtQuick
import QtQuick.Controls
import "Theme.js" as T

Item {
    id: root

    Rectangle {
        anchors.fill: parent
        anchors.margins: 14
        color: T.surface
        radius: 14
        border.color: Qt.rgba(1, 1, 1, 0.04)
        border.width: 1
        clip: true

        Flickable {
            id: flick
            anchors.fill: parent
            anchors.margins: 12
            clip: true
            contentWidth: width
            contentHeight: logText.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            TextEdit {
                id: logText
                width: flick.width
                readOnly: true
                wrapMode: TextEdit.Wrap
                color: T.text
                selectedTextColor: T.text
                selectionColor: T.select
                font.family: T.fontMono
                font.pixelSize: 11
                text: bridge.logText
                textFormat: TextEdit.PlainText
            }
        }
    }

    Connections {
        target: bridge
        function onLogAppended(_line) {
            Qt.callLater(function () {
                flick.contentY = Math.max(0, logText.implicitHeight - flick.height)
            })
        }
    }
}

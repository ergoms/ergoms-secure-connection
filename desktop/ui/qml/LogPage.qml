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

        Rectangle {
            id: toolbar
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 40
            color: "transparent"

            Rectangle {
                id: copyBtn
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                width: copyLabel.implicitWidth + 20
                height: 28
                radius: 8
                color: copyMouse.containsMouse ? T.btnHover : T.btn
                Text {
                    id: copyLabel
                    anchors.centerIn: parent
                    text: "Копировать всё"
                    color: T.text
                    font.pixelSize: 12
                    font.family: T.fontUi
                }
                MouseArea {
                    id: copyMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bridge.copyLog()
                }
            }
        }

        Flickable {
            id: flick
            anchors.top: toolbar.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            anchors.bottomMargin: 12
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

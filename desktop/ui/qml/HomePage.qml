import QtQuick
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 22
        anchors.rightMargin: 22
        anchors.topMargin: 8
        anchors.bottomMargin: 8
        spacing: 12

        Item { Layout.fillHeight: true }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 176
            PowerRing {
                anchors.horizontalCenter: parent.horizontalCenter
                ringColor: bridge.statusColor
                busy: bridge.busy
                active: bridge.active
            }
        }

        Text {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: bridge.statusTitle
            color: T.text
            font.pixelSize: 24
            font.bold: true
            font.family: T.fontUi
        }

        PrimaryButton {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            text: bridge.powerText
            primary: !bridge.active
            danger: bridge.active
            enabled: !bridge.busy
            onClicked: bridge.toggleConnection()
        }

        Text {
            Layout.fillWidth: true
            text: bridge.busyText
            color: T.muted
            font.pixelSize: 11
            font.family: T.fontUi
            horizontalAlignment: Text.AlignHCenter
            opacity: bridge.busy ? 1 : 0
        }

        Item { Layout.fillHeight: true }
    }
}

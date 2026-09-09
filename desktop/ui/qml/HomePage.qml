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
            text: bridge.busy ? bridge.busyText : bridge.statusTitle
            color: T.text
            font.pixelSize: 24
            font.bold: true
            font.family: T.fontUi
            Behavior on opacity { NumberAnimation { duration: 160 } }
        }

        Text {
            visible: !bridge.busy && bridge.statusSub.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            horizontalAlignment: Text.AlignHCenter
            text: bridge.statusSub
            color: T.muted
            font.pixelSize: 12
            font.family: T.fontUi
            wrapMode: Text.WordWrap
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

        Item { Layout.fillHeight: true }
    }
}

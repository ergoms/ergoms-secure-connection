import QtQuick
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 22
        anchors.rightMargin: 22
        anchors.topMargin: 4
        anchors.bottomMargin: 8
        spacing: 12

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

        Text {
            Layout.fillWidth: true
            Layout.topMargin: -8
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

        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 118
                Column {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    Row {
                        width: parent.width
                        Text {
                            text: "Режим"
                            color: T.muted
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: 90
                        }
                        Text {
                            text: bridge.modeLabel
                            color: T.text
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: parent.width - 90
                            elide: Text.ElideMiddle
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    Row {
                        width: parent.width
                        Text {
                            text: "Сервер"
                            color: T.muted
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: 90
                        }
                        Text {
                            text: bridge.serverTarget
                            color: T.text
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: parent.width - 90
                            elide: Text.ElideMiddle
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    Row {
                        width: parent.width
                        Text {
                            text: "Область"
                            color: T.muted
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: 90
                        }
                        Text {
                            text: bridge.scope
                            color: T.text
                            font.pixelSize: 12
                            font.family: T.fontUi
                            width: parent.width - 90
                            elide: Text.ElideMiddle
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            PrimaryButton {
                Layout.fillWidth: true
                text: bridge.tunButtonText
                enabled: !bridge.busy
                onClicked: bridge.toggleTun()
            }
            PrimaryButton {
                Layout.fillWidth: true
                text: "Проверка"
                enabled: !bridge.busy
                onClicked: bridge.probe()
            }
            PrimaryButton {
                Layout.fillWidth: true
                text: "Тест"
                enabled: !bridge.busy
                onClicked: bridge.testBypass()
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Chip { label: "SOCKS :" + bridge.socksPort; lit: bridge.socksUp }
            Chip { label: "HTTP :" + bridge.httpPort; lit: bridge.httpUp }
            Chip { label: "PAC :" + bridge.pacPort; lit: bridge.pacUp }
            Chip { label: "TUN"; lit: bridge.tun }
            Chip { label: "watchdog"; lit: bridge.watchdogUp }
            Chip { label: "SSH :" + bridge.reverseSshPort; lit: bridge.reverseSshUp }
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

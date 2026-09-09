import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: form.height
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
            }

            Column {
                id: form
                width: flick.width - 28
                x: 14
                spacing: 8
                topPadding: 8
                bottomPadding: 16
                enabled: !bridge.active
                opacity: bridge.active ? 0.55 : 1

                Text {
                    visible: bridge.active
                    width: parent.width
                    text: "Отключите VPN, чтобы менять настройки"
                    color: T.warn
                    font.pixelSize: 12
                    font.family: T.fontUi
                    wrapMode: Text.WordWrap
                }

                Text {
                    text: "VPN"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                }
                Card {
                    width: parent.width
                    implicitHeight: vpnCol.implicitHeight + 24
                    Column {
                        id: vpnCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text { text: "Корпоративный VPN"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.corporate ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.applyCorporateMode(v === "1") }
                        }
                        Text { text: "TUN автоматически"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.tunAuto ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => {
                                bridge.settings.tunAuto = (v === "1")
                                if (v === "0")
                                    bridge.settings.killSwitch = false
                            }
                        }
                        Text { text: "Kill switch"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.killSwitch ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => {
                                bridge.settings.killSwitch = (v === "1")
                                if (v === "1")
                                    bridge.settings.tunAuto = true
                            }
                        }
                        Text { text: "Автозапуск с компьютером"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.autostart ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.setAutostart(v === "1") }
                        }
                        Text { text: "Прокси"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.useProxy ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.useProxy = (v === "1") }
                        }
                        SettingField {
                            visible: Boolean(bridge.settings.useProxy) || bridge.corporate
                            height: visible ? implicitHeight : 0
                            label: "Адрес прокси"
                            settingKey: "corporateProxy"
                        }
                    }
                }

                Text {
                    text: "ИНТЕГРАЦИИ"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                    topPadding: 8
                }
                Card {
                    width: parent.width
                    implicitHeight: integCol.implicitHeight + 24
                    Column {
                        id: integCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text { text: "Git через VPN"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.gitProxy ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.gitProxy = (v === "1") }
                        }
                        Text { text: "Docker через VPN"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.dockerProxy ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.dockerProxy = (v === "1") }
                        }
                    }
                }

                Text {
                    text: "СЕРВЕР"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                    topPadding: 8
                }
                Card {
                    width: parent.width
                    implicitHeight: serverCol.implicitHeight + 24
                    Column {
                        id: serverCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        SettingField {
                            label: "Адрес сервера"
                            settingKey: "serverHost"
                        }
                        SettingField {
                            label: "Порт"
                            settingKey: "serverPort"
                        }
                        SettingField {
                            label: "UUID"
                            password: true
                            settingKey: "trUuid"
                        }
                        SettingField {
                            label: "Public key"
                            password: true
                            settingKey: "trPublicKey"
                        }
                        SettingField {
                            label: "Short ID"
                            settingKey: "trShortId"
                        }
                        SettingField {
                            label: "Server name (SNI)"
                            settingKey: "trServerName"
                        }
                    }
                }

                Text {
                    visible: bridge.corporate
                    height: visible ? implicitHeight : 0
                    text: "КОРПОРАТИВНАЯ СЕТЬ"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                    topPadding: 8
                }
                Card {
                    visible: bridge.corporate
                    height: visible ? implicitHeight : 0
                    width: parent.width
                    implicitHeight: corpCol.implicitHeight + 24
                    Column {
                        id: corpCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text { text: "Область трафика"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: String(bridge.settings.socksScope)
                            model: [
                                { label: "Всё", value: "full" },
                                { label: "GitHub + Cursor", value: "github" }
                            ]
                            onActivated: (v) => { bridge.settings.socksScope = v }
                        }
                        SettingField {
                            label: "Исключения (через запятую)"
                            settingKey: "proxyBypass"
                        }
                        Text { text: "SSH с VPS на этот ПК"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.reverseSsh ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.reverseSsh = (v === "1") }
                        }
                        SettingField {
                            visible: Boolean(bridge.settings.reverseSsh)
                            height: visible ? implicitHeight : 0
                            label: "Порт на VPS (127.0.0.1)"
                            settingKey: "reverseSshListen"
                        }
                        SettingField {
                            visible: Boolean(bridge.settings.reverseSsh)
                            height: visible ? implicitHeight : 0
                            label: "Пользователь SSH на VPS"
                            settingKey: "reverseSshVpsUser"
                        }
                        SettingField {
                            visible: Boolean(bridge.settings.reverseSsh)
                            height: visible ? implicitHeight : 0
                            label: "Порт sshd на VPS"
                            settingKey: "reverseSshVpsPort"
                        }
                    }
                }

                Text {
                    width: parent.width
                    text: bridge.dataRoot
                    color: T.muted
                    font.pixelSize: 10
                    font.family: T.fontUi
                    wrapMode: Text.WrapAnywhere
                    topPadding: 6
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            color: T.bg
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.bottomMargin: 10
                anchors.topMargin: 6
                spacing: 8
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Из файла"
                    enabled: !bridge.active
                    onClicked: bridge.importConfigFile()
                }
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Сохранить"
                    primary: true
                    enabled: !bridge.active
                    onClicked: bridge.saveSettings()
                }
            }
        }
    }
}

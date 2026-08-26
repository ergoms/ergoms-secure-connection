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

                Text {
                    text: "КЛИЕНТ"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                }
                Card {
                    width: parent.width
                    implicitHeight: clientCol.implicitHeight + 24
                    Column {
                        id: clientCol
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
                        Text { text: "TUN автоматически"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.tunAuto ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.tunAuto = (v === "1") }
                        }
                        Text { text: "Запрос прав админа"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.tunElevate ? "1" : "0"
                            model: [
                                { label: "Нет", value: "0" },
                                { label: "Да", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.tunElevate = (v === "1") }
                        }
                        SettingField {
                            label: "Порт HTTP-моста"
                            settingKey: "httpBridgePort"
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
                            label: "Порт на VPS (127.0.0.1)"
                            settingKey: "reverseSshListen"
                        }
                        SettingField {
                            label: "Пользователь SSH на VPS"
                            settingKey: "reverseSshVpsUser"
                        }
                        SettingField {
                            label: "Порт sshd на VPS"
                            settingKey: "reverseSshVpsPort"
                        }
                    }
                }

                Text {
                    text: "СЕРВЕР VLESS"
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
                            label: "Локальный порт SOCKS"
                            settingKey: "serverSocks"
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
                        SettingField {
                            label: "Порт transport"
                            settingKey: "trPort"
                        }
                    }
                }

                Text {
                    text: "ПРОКСИ И ИСКЛЮЧЕНИЯ"
                    color: T.muted
                    font.pixelSize: 11
                    font.bold: true
                    font.family: T.fontUi
                    topPadding: 8
                }
                Card {
                    width: parent.width
                    implicitHeight: proxyCol.implicitHeight + 24
                    Column {
                        id: proxyCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        SettingField {
                            label: "Корп. прокси"
                            settingKey: "corporateProxy"
                        }
                        SettingField {
                            label: "Исключения (через запятую)"
                            settingKey: "proxyBypass"
                        }
                        Text { text: "Исключения идут"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: String(bridge.settings.proxyBypassVia)
                            model: [
                                { label: "Напрямую", value: "direct" },
                                { label: "Через Squid", value: "corporate" }
                            ]
                            onActivated: (v) => { bridge.settings.proxyBypassVia = v }
                        }
                        Text { text: "Путь к sing-box"; color: T.muted; font.pixelSize: 11; font.family: T.fontUi }
                        Row {
                            width: parent.width
                            spacing: 8
                            TextField {
                                id: sbPath
                                width: parent.width - 52
                                height: 40
                                text: String(bridge.settings.singBoxPath ?? "")
                                onTextEdited: bridge.settings.singBoxPath = text
                                color: T.text
                                font.pixelSize: 12
                                font.family: T.fontUi
                                leftPadding: 12
                                background: Rectangle {
                                    color: T.surface2
                                    radius: 10
                                }
                            }
                            PrimaryButton {
                                width: 44
                                height: 40
                                text: "…"
                                onClicked: bridge.pickSingBox()
                            }
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
                    onClicked: bridge.importConfigFile()
                }
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Сохранить"
                    primary: true
                    onClicked: bridge.saveSettings()
                }
            }
        }
    }
}

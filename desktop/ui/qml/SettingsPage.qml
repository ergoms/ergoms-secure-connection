import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    readonly property bool tunOn: Boolean(bridge.settings.tunAuto) || Boolean(bridge.settings.killSwitch)
    readonly property bool awgDial: String(bridge.settings.trDial) === "amneziawg"
    readonly property bool missingAppConfig: {
        var host = String(bridge.settings.serverHost || "").replace(/^\s+|\s+$/g, "")
        if (!host || host.indexOf("YOUR_VPS") >= 0)
            return true
        if (root.awgDial)
            return false
        return !String(bridge.settings.trUuid || "").replace(/^\s+|\s+$/g, "")
            || !String(bridge.settings.trPublicKey || "").replace(/^\s+|\s+$/g, "")
    }
    readonly property bool missingAwgConfig: !Boolean(bridge.settings.awgLoaded)

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
            ScrollBar.vertical: AppScrollBar {}

            Column {
                id: form
                width: flick.width - 28
                x: 14
                spacing: 8
                topPadding: 8
                bottomPadding: 16
                enabled: !bridge.active && !bridge.busy
                opacity: (bridge.active || bridge.busy) ? 0.55 : 1

                Text {
                    visible: bridge.active || bridge.busy
                    width: parent.width
                    text: bridge.busy
                          ? "Дождитесь окончания операции"
                          : "Отключите VPN, чтобы менять настройки"
                    color: T.warn
                    font.pixelSize: 12
                    font.family: T.fontUi
                    wrapMode: Text.WordWrap
                }

                SectionLabel { text: "РЕЖИМ" }
                Card {
                    width: parent.width
                    implicitHeight: 36 + 28
                    Segmented {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 14
                        value: bridge.corporate ? "corp" : "home"
                        model: [
                            { label: "Обычный", value: "home" },
                            { label: "Корпоративный", value: "corp" }
                        ]
                        onActivated: (v) => bridge.applyCorporateMode(v === "corp")
                    }
                }

                SettingsSection {
                    title: "ЗАЩИТА"
                    BoolSegmented {
                        label: "Весь трафик через VPN"
                        on: bridge.settings.tunAuto
                        onToggled: (v) => {
                            bridge.settings.tunAuto = v
                            if (!v)
                                bridge.settings.killSwitch = false
                        }
                    }
                    BoolSegmented {
                        label: "Защита при обрыве"
                        on: bridge.settings.killSwitch
                        onToggled: (v) => {
                            bridge.settings.killSwitch = v
                            if (v)
                                bridge.settings.tunAuto = true
                        }
                    }
                    Text {
                        width: parent.width
                        text: "При обрыве интернет будет недоступен, пока вы не отключитесь."
                        color: T.muted
                        font.pixelSize: 11
                        font.family: T.fontUi
                        wrapMode: Text.WordWrap
                    }
                    BoolSegmented {
                        label: "Автозапуск с компьютером"
                        on: bridge.autostart
                        onToggled: (v) => bridge.setAutostart(v)
                    }
                    SettingField {
                        visible: bridge.corporate
                        height: visible ? implicitHeight : 0
                        label: "Адрес прокси"
                        settingKey: "corporateProxy"
                    }
                }

                SettingsSection {
                    title: "ИНТЕГРАЦИИ"
                    BoolSegmented {
                        label: "Git через VPN"
                        on: bridge.settings.gitProxy
                        onToggled: (v) => { bridge.settings.gitProxy = v }
                    }
                    BoolSegmented {
                        label: "Docker через VPN"
                        on: bridge.settings.dockerProxy
                        onToggled: (v) => { bridge.settings.dockerProxy = v }
                    }
                }

                SectionLabel {
                    text: "СЕРВЕР"
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
                        Text { text: "Протокол"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: String(bridge.settings.trDial || (bridge.corporate ? "vless-reality" : "amneziawg"))
                            model: [
                                { label: "Reality", value: "vless-reality" },
                                { label: "AWG", value: "amneziawg" }
                            ]
                            onActivated: (v) => { bridge.settings.trDial = v }
                        }
                    }
                }

                SectionLabel {
                    visible: !root.awgDial
                    height: visible ? implicitHeight : 0
                    text: "ПОДКЛЮЧЕНИЕ"
                    topPadding: 8
                }
                Card {
                    visible: !root.awgDial
                    height: visible ? implicitHeight : 0
                    width: parent.width
                    implicitHeight: visible ? realityCol.implicitHeight + 24 : 0
                    Column {
                        id: realityCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        TransportFields {
                            fields: [
                                { label: "UUID", key: "trUuid", password: true },
                                { label: "Public key", key: "trPublicKey", password: true },
                                { label: "Short ID", key: "trShortId" },
                                { label: "SNI (dest)", key: "trServerName" }
                            ]
                        }
                    }
                }

                SectionLabel {
                    visible: root.awgDial
                    height: visible ? implicitHeight : 0
                    text: "ПОДКЛЮЧЕНИЕ"
                    topPadding: 8
                }
                Card {
                    visible: root.awgDial
                    height: visible ? implicitHeight : 0
                    width: parent.width
                    implicitHeight: visible ? awgCol.implicitHeight + 24 : 0
                    Column {
                        id: awgCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text {
                            visible: Boolean(bridge.settings.awgLoaded)
                            width: parent.width
                            height: visible ? implicitHeight : 0
                            text: String(bridge.settings.awgSummary || "")
                            color: T.text
                            font.pixelSize: 12
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        PrimaryButton {
                            width: parent.width
                            text: Boolean(bridge.settings.awgLoaded) ? "Заменить .conf" : "Загрузить .conf"
                            onClicked: bridge.importAwgConfFile()
                        }
                    }
                }

                SettingsSection {
                    title: "УДАЛЁННЫЙ ДОСТУП"
                    BoolSegmented {
                        label: "Доступ с сервера на этот ПК"
                        on: bridge.settings.reverseSsh
                        onToggled: (v) => { bridge.settings.reverseSsh = v }
                    }
                    SettingField {
                        visible: Boolean(bridge.settings.reverseSsh)
                        height: visible ? implicitHeight : 0
                        label: "Порт на сервере"
                        settingKey: "reverseSshListen"
                    }
                    SettingField {
                        visible: Boolean(bridge.settings.reverseSsh)
                        height: visible ? implicitHeight : 0
                        label: "Пользователь SSH"
                        settingKey: "reverseSshVpsUser"
                    }
                    SettingField {
                        visible: Boolean(bridge.settings.reverseSsh)
                        height: visible ? implicitHeight : 0
                        label: "Порт SSH на сервере"
                        settingKey: "reverseSshVpsPort"
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: missingCol.implicitHeight + 62
            color: T.bg

            Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Qt.rgba(1, 1, 1, 0.05)
            }

            Column {
                id: missingCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.topMargin: 10
                spacing: 8

                MissingBanner {
                    visible: root.missingAppConfig
                    height: visible ? implicitHeight : 0
                    message: "Нет общего конфига"
                    onActivated: bridge.importConfigFile()
                }
                MissingBanner {
                    visible: root.missingAwgConfig
                    height: visible ? implicitHeight : 0
                    message: "Нет конфига Amnezia"
                    onActivated: bridge.importAwgConfFile()
                }
            }

            RowLayout {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.bottomMargin: 10
                height: 44
                spacing: 8
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Из файла"
                    enabled: !bridge.active && !bridge.busy
                    onClicked: bridge.importConfigFile()
                }
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Копировать"
                    enabled: !bridge.active && !bridge.busy
                    onClicked: bridge.exportConfigFile()
                }
                PrimaryButton {
                    Layout.fillWidth: true
                    text: "Сохранить"
                    primary: true
                    enabled: !bridge.active && !bridge.busy
                    onClicked: bridge.saveSettings()
                }
            }
        }
    }
}

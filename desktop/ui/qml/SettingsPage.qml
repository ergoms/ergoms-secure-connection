import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    readonly property bool tunOn: Boolean(bridge.settings.tunAuto) || Boolean(bridge.settings.killSwitch)
    readonly property bool awgDial: String(bridge.settings.trDial) === "amneziawg"
    readonly property bool settingsIncomplete: {
        var host = String(bridge.settings.serverHost || "").replace(/^\s+|\s+$/g, "")
        if (!host || host.indexOf("YOUR_VPS") >= 0)
            return true
        if (root.awgDial) {
            return !String(bridge.settings.awgPrivateKey || "").replace(/^\s+|\s+$/g, "")
                || !String(bridge.settings.awgPeerPublicKey || "").replace(/^\s+|\s+$/g, "")
        }
        return !String(bridge.settings.trUuid || "").replace(/^\s+|\s+$/g, "")
            || !String(bridge.settings.trPublicKey || "").replace(/^\s+|\s+$/g, "")
    }

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

                Text {
                    visible: root.settingsIncomplete && !bridge.active && !bridge.busy
                    width: parent.width
                    text: "Не хватает данных для подключения. Загрузите конфиг."
                    color: T.warn
                    font.pixelSize: 12
                    font.family: T.fontUi
                    wrapMode: Text.WordWrap
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.awgDial ? bridge.importAwgConfFile() : bridge.importConfigFile()
                    }
                }

                SectionLabel { text: "VPN" }
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

                        BoolSegmented {
                            label: "Корпоративный VPN"
                            on: bridge.corporate
                            onToggled: (v) => bridge.applyCorporateMode(v)
                        }
                        BoolSegmented {
                            label: "TUN (весь трафик)"
                            on: bridge.settings.tunAuto
                            onToggled: (v) => {
                                bridge.settings.tunAuto = v
                                if (!v)
                                    bridge.settings.killSwitch = false
                            }
                        }
                        Text {
                            width: parent.width
                            text: root.tunOn
                                  ? "Весь трафик устройства идёт через VPN."
                                  : "Без этого режима часть приложений может ходить в интернет напрямую."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        Text { text: "Системный прокси Windows"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            enabled: false
                            value: root.tunOn ? "0" : "1"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "PAC", value: "1" }
                            ]
                        }
                        Text {
                            width: parent.width
                            text: root.tunOn
                                  ? "Не используется, пока включён полный туннель."
                                  : "Включается автоматически при подключении."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
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
                            text: "При обрыве VPN интернет будет недоступен, пока вы не отключитесь."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            width: parent.width
                            text: "Пока включён полный туннель, закрываются Secure DNS и WebRTC host-IP. Перезапустите Chrome или Edge. Чужой VPN с default-маршрутом будет снят."
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
                        Text {
                            visible: bridge.corporate
                            height: visible ? implicitHeight : 0
                            text: "Прокси организации"
                            color: T.muted
                            font.pixelSize: 11
                            font.weight: Font.Medium
                            font.family: T.fontUi
                        }
                        SettingField {
                            visible: bridge.corporate
                            height: visible ? implicitHeight : 0
                            label: "Адрес прокси"
                            settingKey: "corporateProxy"
                        }
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

                        Text {
                            visible: bridge.corporate
                            width: parent.width
                            height: visible ? implicitHeight : 0
                            text: "В офисе по умолчанию Reality через Squid. Можно выбрать AWG — он идёт минуя прокси."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
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
                            width: parent.width
                            text: "Загрузка конфига AmneziaWG"
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            width: parent.width
                            text: Boolean(bridge.settings.awgLoaded)
                                  ? String(bridge.settings.awgSummary || "")
                                  : "Файл ещё не загружен."
                            color: Boolean(bridge.settings.awgLoaded) ? T.text : T.warn
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

                SectionLabel {
                    visible: bridge.corporate
                    height: visible ? implicitHeight : 0
                    text: "КОРПОРАТИВНАЯ СЕТЬ"
                    topPadding: 8
                }
                Card {
                    visible: bridge.corporate
                    height: visible ? implicitHeight : 0
                    width: parent.width
                    implicitHeight: visible ? corpCol.implicitHeight + 24 : 0
                    Column {
                        id: corpCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text { text: "Область трафика"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
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
                    }
                }

                SettingsSection {
                    title: "SSH С СЕРВЕРА"
                    Text {
                        width: parent.width
                        text: "Удалённый доступ к этому компьютеру, пока VPN включён."
                        color: T.muted
                        font.pixelSize: 11
                        font.family: T.fontUi
                        wrapMode: Text.WordWrap
                    }
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

            Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Qt.rgba(1, 1, 1, 0.05)
            }

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

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    readonly property bool tunOn: Boolean(bridge.settings.tunAuto) || Boolean(bridge.settings.killSwitch)
    readonly property bool hy2Dial: !bridge.corporate && String(bridge.settings.trDial) === "hysteria2"
    readonly property bool awgDial: !bridge.corporate && String(bridge.settings.trDial) === "amneziawg"
    readonly property bool settingsIncomplete: {
        var host = String(bridge.settings.serverHost || "").replace(/^\s+|\s+$/g, "")
        if (!host || host.indexOf("YOUR_VPS") >= 0)
            return true
        if (root.awgDial) {
            return !String(bridge.settings.awgPrivateKey || "").replace(/^\s+|\s+$/g, "")
                || !String(bridge.settings.awgPeerPublicKey || "").replace(/^\s+|\s+$/g, "")
        }
        if (root.hy2Dial) {
            return !String(bridge.settings.hy2Password || "").replace(/^\s+|\s+$/g, "")
        }
        return !String(bridge.settings.trUuid || "").replace(/^\s+|\s+$/g, "")
            || !String(bridge.settings.trPublicKey || "").replace(/^\s+|\s+$/g, "")
    }
    property bool awgAdvanced: false

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
                        onClicked: bridge.importConfigFile()
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

                        Text { text: "Корпоративный VPN"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.corporate ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.applyCorporateMode(v === "1") }
                        }
                        Text { text: "TUN (весь трафик)"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
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
                        Text { text: "Защита при обрыве"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
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
                        Text {
                            width: parent.width
                            text: "При обрыве VPN интернет будет недоступен, пока вы не отключитесь."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        Text { text: "Автозапуск с компьютером"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.autostart ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.setAutostart(v === "1") }
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

                SectionLabel {
                    text: "ИНТЕГРАЦИИ"
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

                        Text { text: "Git через VPN"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
                        Segmented {
                            width: parent.width
                            value: bridge.settings.gitProxy ? "1" : "0"
                            model: [
                                { label: "Выкл", value: "0" },
                                { label: "Вкл", value: "1" }
                            ]
                            onActivated: (v) => { bridge.settings.gitProxy = (v === "1") }
                        }
                        Text { text: "Docker через VPN"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
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
                            enabled: !bridge.corporate
                            value: bridge.corporate ? "vless-reality" : String(bridge.settings.trDial || "hysteria2")
                            model: [
                                { label: "Reality", value: "vless-reality" },
                                { label: "Hy2", value: "hysteria2" },
                                { label: "AWG", value: "amneziawg" }
                            ]
                            onActivated: (v) => { bridge.settings.trDial = v }
                        }
                    }
                }

                SectionLabel {
                    visible: !root.hy2Dial && !root.awgDial
                    height: visible ? implicitHeight : 0
                    text: "ПОДКЛЮЧЕНИЕ"
                    topPadding: 8
                }
                Card {
                    visible: !root.hy2Dial && !root.awgDial
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
                            text: "В этой сети доступен только выбранный способ подключения."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
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
                            label: "SNI (dest)"
                            settingKey: "trServerName"
                        }
                    }
                }

                SectionLabel {
                    visible: root.hy2Dial
                    height: visible ? implicitHeight : 0
                    text: "ПОДКЛЮЧЕНИЕ"
                    topPadding: 8
                }
                Card {
                    visible: root.hy2Dial
                    height: visible ? implicitHeight : 0
                    width: parent.width
                    implicitHeight: visible ? hy2Col.implicitHeight + 24 : 0
                    Column {
                        id: hy2Col
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text {
                            width: parent.width
                            text: "Параметры выбранного способа подключения."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        SettingField {
                            label: "Пароль"
                            password: true
                            settingKey: "hy2Password"
                        }
                        SettingField {
                            label: "UDP порт"
                            settingKey: "hy2Port"
                        }
                        SettingField {
                            label: "SNI"
                            settingKey: "hy2ServerName"
                        }
                        SettingField {
                            label: "Обфускация"
                            password: true
                            settingKey: "hy2Obfs"
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
                            text: "Параметры выбранного способа подключения."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        SettingField {
                            label: "Private key"
                            password: true
                            settingKey: "awgPrivateKey"
                        }
                        SettingField {
                            label: "Peer public key"
                            password: true
                            settingKey: "awgPeerPublicKey"
                        }
                        SettingField {
                            label: "Адрес туннеля"
                            settingKey: "awgAddress"
                        }
                        SettingField {
                            label: "UDP порт"
                            settingKey: "awgPort"
                        }
                        SettingField {
                            label: "Preshared key"
                            password: true
                            settingKey: "awgPresharedKey"
                        }
                        Text {
                            width: parent.width
                            text: root.awgAdvanced ? "Обфускация ▾" : "Обфускация ▸"
                            color: T.muted
                            font.pixelSize: 11
                            font.weight: Font.Medium
                            font.family: T.fontUi
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.awgAdvanced = !root.awgAdvanced
                            }
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "MTU"
                            settingKey: "awgMtu"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "Jc"
                            settingKey: "awgJc"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "Jmin"
                            settingKey: "awgJmin"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "Jmax"
                            settingKey: "awgJmax"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "S1"
                            settingKey: "awgS1"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "S2"
                            settingKey: "awgS2"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "H1"
                            settingKey: "awgH1"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "H2"
                            settingKey: "awgH2"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "H3"
                            settingKey: "awgH3"
                        }
                        SettingField {
                            visible: root.awgAdvanced
                            height: visible ? implicitHeight : 0
                            label: "H4"
                            settingKey: "awgH4"
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

                SectionLabel {
                    text: "SSH С СЕРВЕРА"
                    topPadding: 8
                }
                Card {
                    width: parent.width
                    implicitHeight: sshCol.implicitHeight + 24
                    Column {
                        id: sshCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10

                        Text {
                            width: parent.width
                            text: "Удалённый доступ к этому компьютеру, пока VPN включён."
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontUi
                            wrapMode: Text.WordWrap
                        }
                        Text { text: "Доступ с сервера на этот ПК"; color: T.muted; font.pixelSize: 11; font.weight: Font.Medium; font.family: T.fontUi }
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

import QtQuick
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    readonly property bool missingAppConfig: {
        var host = String(bridge.settings.serverHost || "").replace(/^\s+|\s+$/g, "")
        if (!host || host.indexOf("YOUR_VPS") >= 0)
            return true
        if (String(bridge.settings.trDial) === "amneziawg")
            return false
        return !String(bridge.settings.trUuid || "").replace(/^\s+|\s+$/g, "")
            || !String(bridge.settings.trPublicKey || "").replace(/^\s+|\s+$/g, "")
    }
    readonly property bool missingAwgConfig: !Boolean(bridge.settings.awgLoaded)

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
            font.pixelSize: 22
            font.weight: Font.DemiBold
            font.letterSpacing: 0.2
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
            visible: bridge.canReconnect
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 50 : 0
            text: "Переподключить"
            primary: true
            enabled: !bridge.busy
            onClicked: bridge.reconnectConnection()
        }

        PrimaryButton {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            text: bridge.powerText
            primary: !bridge.active && !bridge.canReconnect
            danger: bridge.active || bridge.canReconnect
            enabled: !bridge.busy
            onClicked: bridge.toggleConnection()
        }

        Item { Layout.fillHeight: true }

        MissingBanner {
            visible: root.missingAppConfig
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            message: "Нет общего конфига"
            onActivated: bridge.importConfigFile()
        }
        MissingBanner {
            visible: root.missingAwgConfig
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            message: "Нет конфига Amnezia"
            onActivated: bridge.importAwgConfFile()
        }
    }
}

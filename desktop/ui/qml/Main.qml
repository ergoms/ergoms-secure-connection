import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Window
import "Theme.js" as T

ApplicationWindow {
    id: win
    title: "ERGOMS SECURE CONNECTION"
    width: 420
    height: 680
    minimumWidth: 420
    maximumWidth: 420
    minimumHeight: 680
    maximumHeight: 680
    visible: true
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint
    font.family: Qt.platform.os === "windows" ? T.fontUi : "sans-serif"
    Material.theme: Material.Dark
    Material.accent: T.accent
    Material.background: T.bg
    Material.foreground: T.text
    Material.primary: T.surface

    onClosing: (event) => {
        event.accepted = false
        bridge.hideWindow()
    }

    Rectangle {
        id: chrome
        anchors.fill: parent
        color: T.bg
        radius: 16
        border.color: T.windowBorder
        border.width: 1
        clip: true

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            Rectangle {
                id: titleBar
                Layout.fillWidth: true
                Layout.preferredHeight: 52
                color: "transparent"

                DragHandler {
                    target: null
                    onActiveChanged: if (active) win.startSystemMove()
                }

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 18
                    anchors.right: chromeBtns.left
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: "ERGOMS SECURE CONNECTION"
                    color: T.text
                    elide: Text.ElideRight
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.6
                    font.family: T.fontUi
                }

                Row {
                    id: chromeBtns
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4

                    ChromeButton {
                        kind: "min"
                        onClicked: win.showMinimized()
                    }
                    ChromeButton {
                        kind: "close"
                        onClicked: bridge.hideWindow()
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: Qt.rgba(1, 1, 1, 0.05)
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: bridge.page === "home" ? 0 : (bridge.page === "settings" ? 1 : (bridge.page === "exceptions" ? 2 : 3))

                HomePage {}
                SettingsPage {}
                ExceptionsPage {}
                LogPage {}
            }

            NavBar {
                Layout.fillWidth: true
            }
        }

        ToastHost {
            id: toasts
        }
    }

    Connections {
        target: bridge
        function onToast(message, kind) {
            if (bridge.page !== "log")
                toasts.show(message, kind)
        }
        function onHideRequested() { win.hide() }
        function onShowRequested() {
            win.visible = true
            if (win.visibility === Window.Minimized)
                win.showNormal()
            else
                win.show()
            win.raise()
            win.requestActivate()
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Window
import "Theme.js" as T

ApplicationWindow {
    id: win
    title: "ERGOMS VPN"
    width: 420
    height: 680
    minimumWidth: 420
    maximumWidth: 420
    minimumHeight: 680
    maximumHeight: 680
    visible: true
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
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
                Layout.preferredHeight: 48
                color: "transparent"

                DragHandler {
                    target: null
                    onActiveChanged: if (active) win.startSystemMove()
                }

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 18
                    anchors.verticalCenter: parent.verticalCenter
                    text: "ERGOMS VPN"
                    color: T.text
                    font.pixelSize: 16
                    font.bold: true
                    font.family: T.fontUi
                }

                Row {
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6

                    Rectangle {
                        width: 28
                        height: 28
                        radius: 8
                        color: minMouse.containsMouse ? T.btn : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "–"
                            color: T.muted
                            font.pixelSize: 16
                        }
                        MouseArea {
                            id: minMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.showMinimized()
                        }
                    }
                    Rectangle {
                        width: 28
                        height: 28
                        radius: 8
                        color: closeMouse.containsMouse ? T.danger : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "×"
                            color: closeMouse.containsMouse ? T.text : T.muted
                            font.pixelSize: 16
                        }
                        MouseArea {
                            id: closeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: bridge.hideWindow()
                        }
                    }
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: bridge.page === "home" ? 0 : (bridge.page === "settings" ? 1 : 2)

                HomePage {}
                SettingsPage {}
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
        function onToast(message, kind) { toasts.show(message, kind) }
        function onHideRequested() { win.hide() }
        function onShowRequested() {
            win.show()
            win.raise()
            win.requestActivate()
        }
    }
}

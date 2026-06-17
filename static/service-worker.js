self.addEventListener("push", function (event) {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "Edge2 Inventory Alert";
  const options = {
    body: data.body || "Alert triggered.",
    icon: "/static/icon.png",
    badge: "/static/icon.png",
    vibrate: [200, 100, 200, 100, 200],
    requireInteraction: true,
    tag: data.tag || "edge2-alert",
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data.url || "/"));
});

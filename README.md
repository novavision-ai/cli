# Novavision CLI

NovaVision CLI offers an interface for managing servers and applications locally. It allows you to register and install a server, deploy, and manage an app using Docker Compose.

NovaVision simplifies the process of setting up and managing servers, allowing you to deploy and run applications on edge, local, and cloud servers.

---

## Installation

Install NovaVision CLI using pip:

```bash
pip install novavision-cli
```

---

## Features

### **install**  
Registers a server (edge, local, or cloud) in your system and performs its installation on the device.

```bash
novavision install [edge|local|cloud] <USER_TOKEN>
```

**Parameters**  
- `device-type`: Specifies the server type. Options: `edge`, `local`, or `cloud`.  
- `USER_TOKEN`: User token required for registering and installing the server.

---

### **start**  
Launches the server's or application's Docker Compose environment, starting the server or application if it isn’t already running.

```bash
novavision start [server|app] --id <APP_ID>
```

**Parameters**  
- `--id <APP_ID>` *(Optional, required only for apps)*: Specifies which application to start.

---

### **stop**  
Stops the running server or application by shutting down its Docker Compose environment.

```bash
novavision stop [server|app] --id <APP_ID>
```

**Parameters**  
- `--id <APP_ID>` *(Optional, required only for apps)*: Specifies which application to stop.

---

### **deploy** *(Coming Soon)*  
Downloads an application using a provided app ID and integrates it into the server environment.

```bash
novavision deploy <APP_ID>
```
# Novavision CLI

NovaVision CLI offers an interface for managing servers and applications locally. It allows you to register and install a server, deploy, and manage an app using Docker Compose.

NovaVision simplifies the process of setting up and managing servers, allowing you to deploy and run applications on edge, local, and cloud servers.

---

## Installation

Install NovaVision CLI using pipx:

```bash
# Install pipx if not already installed
python -m pip install --user pipx
python -m pipx ensurepath

# Install NovaVision CLI with pipx
pipx install novavision-cli

# Verify Installation
novavision --help
```

---

## Features

### **install**  
Performs creation and installation of a device on your system.

```bash
novavision install [edge|local|cloud] <USER_TOKEN> --host <HOST> --workspace <USER_WORKSPACE_NAME>
```

**Parameters**  
- `DEVICE_TYPE`: Specifies the server type. Options: `edge`, `local`, or `cloud`.  
- `USER_TOKEN`: User token required for registering and installing the server.
- `--host`: User can specify which host will be used for creating device. Default: `alfa.suite.novavision.ai`. Choices: `alfa.suite.novavision.ai | suite.novavision.ai`
- `--workspace`: User can specify which workspace will be used for creating device. User must type the name of the workspace they have. If this parameter is not entered, workspace selection will be performed while device creation. 

---

### **novavision start**  
Launches the server's or application's Docker Compose environment, starting the server or application if it isn’t already running.

```bash
novavision start [server|app] --id <APP_ID>
```

**Parameters**  
- `--id <APP_ID>` *(Optional, required only for apps)*: Specifies which application to start.

The parent server must be running before starting an app.

---

### **stop**  
Stops the running server or application by shutting down its Docker Compose environment.

```bash
novavision stop [server|app] --id <APP_ID>
```

**Parameters**  
- `--id <APP_ID>` *(Optional, required only for apps)*: Specifies which application to stop.
- `--close-apps`: When stopping a server, also stop apps belonging to that server.

The parent server must be running before stopping an app.

---

### **service**  
Enables, disables, or shows status for automatic server startup.

```bash
novavision service [enable|disable|status] server --id <SERVER_ID> --apps <APP_ID>
```

**Parameters**  
- `--id <SERVER_ID>` *(Optional)*: Specifies which server to manage. If omitted, you will be asked to select one.
- `--apps` *(Optional, enable only)*: App IDs to start after the server. Use `"*"` to start all apps.

Linux requires sudo. Windows requires an Administrator terminal. On macOS and Windows, Docker Desktop must start automatically.

# Novavision CLI

NovaVision CLI offers an interface for managing servers and applications locally. It allows you to register and install a server, deploy, and manage an app using Docker Compose.

NovaVision simplifies the process of setting up and managing servers, allowing you to deploy and run applications on edge, local, and cloud servers.

---

## Installation

Install NovaVision CLI with pip:

```bash
pip install novavision-cli

# Verify installation
novavision --version
```

pipx is optional. Use it if you want the CLI in an isolated environment:

```bash
pipx install novavision-cli
```

---

## Features

### **install**  
Performs creation and installation of a device on your system.

```bash
novavision install [edge|local|cloud] <USER_TOKEN> --host <HOST> --workspace <USER_WORKSPACE_NAME> --port <PORT> --non-interactive
```

**Parameters**  
- `DEVICE_TYPE`: Specifies the server type. Options: `edge`, `local`, or `cloud`.  
- `USER_TOKEN`: User token required for registering and installing the server.
- `--host`: User can specify which host will be used for creating device. Default: `alfa.suite.novavision.ai`. Choices: `alfa.suite.novavision.ai | suite.novavision.ai`
- `--workspace`: User can specify which workspace will be used for creating device. User must type the name of the workspace they have. If this parameter is not entered, workspace selection will be performed while device creation. 
- `--port`: Server API port. Skips the port prompt when set.
- `--non-interactive`: Skip prompts. Requires `--workspace`. Uses port `7001` if `--port` is omitted. Fails instead of asking which device to delete when the account is at its device limit.

---

### **uninstall**  
Stops a local server, deletes the registered device from Suite, and removes the local server folder.

```bash
novavision uninstall server <USER_TOKEN> --id <SERVER_ID>
```

**Parameters**  
- `USER_TOKEN`: User token required to delete the device on the host.
- `--id`: Server folder ID, or the device ID stored in server metadata.

---

### **start**  
Launches the server's or application's Docker Compose environment, starting the server or application if it isn’t already running.

```bash
novavision start [server|app] --id <ID>
```

**Parameters**  
- `--id`: Server folder ID when starting a server. Required App ID when starting an app.

The parent server must be running before starting an app.

---

### **stop**  
Stops the running server or application by shutting down its Docker Compose environment.

```bash
novavision stop [server|app] --id <ID>
```

**Parameters**  
- `--id`: Server folder ID when stopping a server. Required App ID when stopping an app.
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

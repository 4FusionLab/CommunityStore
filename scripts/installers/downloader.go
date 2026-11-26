package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

const (
	repo       = "4FusionLab/CommunityStore"
	product    = "CommunityStore"
	productID  = "08b17a72-c1c4-4c56-b7b0-eeddf157ee43"
	archiveMax = 32 << 20
	payloadMax = 128 << 20
)

var (
	stableTag          = regexp.MustCompile(`^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
	errExists          = errors.New("Community Store already exists")
	errNoRelease       = errors.New("no stable Community Store release has been published")
	reportProgress     = func(string) {}
	writeSetupProgress = func(line string) { fmt.Println(line) }
	cleanupWindows     func(string, string) error
)

type releaseAsset struct {
	Name   string `json:"name"`
	State  string `json:"state"`
	Digest string `json:"digest"`
}

func download(client *http.Client, url string, limit int64) ([]byte, int, error) {
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("User-Agent", "4FusionLab-CommunityStore-Installer")
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, resp.StatusCode, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if int64(len(data)) > limit {
		return nil, resp.StatusCode, errors.New("download exceeds size limit")
	}
	return data, resp.StatusCode, err
}

func latestWebTag(client *http.Client, webReleases string) (string, error) {
	// Inspect only GitHub's first redirect, without fetching the release page.
	probe := *client
	probe.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	req, err := http.NewRequest(http.MethodGet, webReleases+"/latest", nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "4FusionLab-CommunityStore-Installer")
	response, err := probe.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	switch response.StatusCode {
	case http.StatusMovedPermanently, http.StatusFound, http.StatusSeeOther, http.StatusTemporaryRedirect, http.StatusPermanentRedirect:
	case http.StatusNotFound:
		return "", errNoRelease
	default:
		return "", fmt.Errorf("expected a release redirect, got HTTP %d", response.StatusCode)
	}
	location, err := response.Location()
	if err != nil {
		return "", err
	}
	// A canonical release URL has exactly our origin and repository, plus one numeric stable tag.
	tag, found := strings.CutPrefix(location.String(), webReleases+"/tag/")
	if !found || !stableTag.MatchString(tag) {
		return "", errors.New("unexpected release redirect target")
	}
	return tag, nil
}

func releaseMetadata(client *http.Client, releaseURL, webReleases string) (string, string, error) {
	metadata, status, err := download(client, releaseURL, 1<<20)
	if status == http.StatusForbidden || status == http.StatusTooManyRequests {
		tag, err := latestWebTag(client, webReleases)
		return tag, "", err
	}
	if status == http.StatusNotFound {
		return "", "", errNoRelease
	}
	if err != nil {
		return "", "", err
	}
	var release struct {
		Tag        string         `json:"tag_name"`
		Prerelease bool           `json:"prerelease"`
		Draft      bool           `json:"draft"`
		Assets     []releaseAsset `json:"assets"`
	}
	if err := json.Unmarshal(metadata, &release); err != nil {
		return "", "", err
	}
	if release.Draft || release.Prerelease || !stableTag.MatchString(release.Tag) {
		return "", "", errors.New("unexpected release tag or channel")
	}

	version := release.Tag[1:]
	name := product + "-" + version + ".zip"
	assets := make(map[string]releaseAsset)
	for _, asset := range release.Assets {
		if (asset.Name == name || asset.Name == name+".sha256") && asset.State == "uploaded" {
			if _, exists := assets[asset.Name]; exists {
				return "", "", errors.New("duplicate release asset")
			}
			assets[asset.Name] = asset
		}
	}
	if len(assets) != 2 {
		return "", "", errors.New("release is missing the ZIP or its SHA-256 file")
	}
	return release.Tag, assets[name].Digest, nil
}

func fetchRelease(client *http.Client, releaseURL, assetsURL string) ([]byte, string, error) {
	reportProgress("release")
	tag, digest, err := releaseMetadata(client, releaseURL, strings.TrimSuffix(assetsURL, "/download"))
	if err != nil {
		return nil, "", err
	}
	version := tag[1:]
	name := product + "-" + version + ".zip"
	reportProgress("download")
	base := assetsURL + "/" + tag + "/" + name
	checksum, _, err := download(client, base+".sha256", 1024)
	if err != nil {
		return nil, "", err
	}
	fields := strings.Fields(string(checksum))
	if len(fields) != 2 || fields[1] != name {
		return nil, "", errors.New("unexpected SHA-256 filename or format")
	}
	sum, err := hex.DecodeString(fields[0])
	if err != nil || len(sum) != sha256.Size {
		return nil, "", errors.New("invalid SHA-256")
	}
	if digest != "" && !strings.EqualFold(digest, "sha256:"+fields[0]) {
		return nil, "", errors.New("release metadata and checksum disagree")
	}
	archive, _, err := download(client, base, archiveMax)
	if err != nil {
		return nil, "", err
	}
	reportProgress("verify")
	actual := sha256.Sum256(archive)
	if !bytes.Equal(actual[:], sum) {
		return nil, "", errors.New("ZIP SHA-256 verification failed")
	}
	return archive, version, nil
}

func extractFile(member *zip.File, path string, remaining int64) (int64, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return 0, err
	}
	source, err := member.Open()
	if err != nil {
		return 0, err
	}
	defer source.Close()
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return 0, err
	}
	size, copyErr := io.Copy(file, io.LimitReader(source, remaining+1))
	return size, errors.Join(copyErr, file.Close())
}

func extract(archive []byte, stage, version string) error {
	bundle, err := zip.NewReader(bytes.NewReader(archive), int64(len(archive)))
	if err != nil {
		return err
	}
	if len(bundle.File) > 4096 {
		return errors.New("too many archive entries")
	}
	var size int64
	for _, member := range bundle.File {
		name := strings.TrimSuffix(member.Name, "/")
		if !fs.ValidPath(name) || strings.ContainsAny(name, "\\:") ||
			(name != product && !strings.HasPrefix(name, product+"/")) ||
			!filepath.IsLocal(filepath.FromSlash(name)) {
			return errors.New("unexpected archive path")
		}
		path := filepath.Join(stage, filepath.FromSlash(name))
		if member.FileInfo().IsDir() {
			if err := os.MkdirAll(path, 0755); err != nil {
				return err
			}
			continue
		}
		remaining := payloadMax - size
		if name == product || !member.Mode().IsRegular() || member.UncompressedSize64 > uint64(remaining) {
			return errors.New("invalid archive entry")
		}
		written, err := extractFile(member, path, remaining)
		if err != nil {
			return err
		}
		size += written
		if size > payloadMax {
			return errors.New("extracted files exceed size limit")
		}
	}
	root := filepath.Join(stage, product)
	manifest, err := os.ReadFile(filepath.Join(root, product+".manifest"))
	if err != nil {
		return err
	}
	var info struct {
		ID           string
		Type         string
		Version      string
		Product      string `json:"autodeskProduct"`
		RunOnStartup bool
		SupportedOS  string
	}
	platform := runtime.GOOS
	if platform == "darwin" {
		platform = "mac"
	}
	if err := json.Unmarshal(manifest, &info); err != nil || info.ID != productID ||
		info.Type != "addin" || info.Product != "Fusion360" || info.Version != version ||
		!info.RunOnStartup || !strings.Contains("|"+info.SupportedOS+"|", "|"+platform+"|") {
		return errors.New("release does not contain the expected Fusion add-in manifest")
	}
	entry, err := os.Stat(filepath.Join(root, product+".py"))
	if err != nil || !entry.Mode().IsRegular() {
		return errors.New("Fusion entry point is missing")
	}
	return nil
}

func absent(path string) error {
	if _, err := os.Lstat(path); err == nil {
		return errExists
	} else if !os.IsNotExist(err) {
		return err
	}
	return nil
}

func install(autodesk string, fetch func() ([]byte, string, error)) (int, error) {
	addins := filepath.Join(autodesk, "FusionAddins")
	destination := filepath.Join(addins, product)
	for _, path := range []string{destination, filepath.Join(autodesk, "Autodesk Fusion 360", "API", "AddIns", product)} {
		if err := absent(path); err != nil {
			return 30, err
		}
	}
	archive, version, err := fetch()
	if err != nil {
		return 20, err
	}
	if err := os.MkdirAll(autodesk, 0755); err != nil {
		return 30, err
	}
	stage, err := os.MkdirTemp(autodesk, ".community-store-install-")
	if err != nil {
		return 30, err
	}
	defer os.RemoveAll(stage)
	if err := extract(archive, stage, version); err != nil {
		return 20, err
	}
	reportProgress("install")
	if err := os.MkdirAll(addins, 0755); err != nil {
		return 30, err
	}
	if err := absent(destination); err != nil {
		return 30, err
	}
	if err := os.Rename(filepath.Join(stage, product), destination); err != nil {
		return 30, err
	}
	reportProgress("complete")
	return 0, nil
}

func main() {
	if len(os.Args) == 4 && os.Args[1] == "--recycle-setup" && cleanupWindows != nil {
		if err := cleanupWindows(os.Args[2], os.Args[3]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	showDiagnostics := true
	if len(os.Args) == 2 {
		switch os.Args[1] {
		case "--progress":
			reportProgress = func(stage string) { fmt.Println(stage) }
		case "--progress-en", "--progress-ru":
			showDiagnostics = false
			labels := map[string][2]string{
				"release":  {"Finding the latest release", "Ищем последний релиз"},
				"download": {"Downloading Community Store", "Скачиваем Community Store"},
				"verify":   {"Checking the download", "Проверяем загруженный архив"},
				"install":  {"Adding Community Store to Fusion", "Добавляем Community Store в Fusion"},
				"complete": {"Community Store is ready", "Community Store готов к запуску"},
			}
			language := 0
			if os.Args[1] == "--progress-ru" {
				language = 1
			}
			reportProgress = func(stage string) { writeSetupProgress(labels[stage][language]) }
		}
	}
	config, err := os.UserConfigDir()
	if err != nil {
		if showDiagnostics {
			fmt.Fprintln(os.Stderr, err)
		}
		os.Exit(30)
	}
	client := &http.Client{
		Timeout: 60 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			host := req.URL.Host
			if len(via) >= 5 || req.URL.Scheme != "https" ||
				(host != "github.com" && host != "release-assets.githubusercontent.com" &&
					host != "objects.githubusercontent.com") {
				return errors.New("unexpected release download redirect")
			}
			return nil
		},
	}
	status, err := install(filepath.Join(config, "Autodesk"), func() ([]byte, string, error) {
		return fetchRelease(client, "https://api.github.com/repos/"+repo+"/releases/latest",
			"https://github.com/"+repo+"/releases/download")
	})
	if err != nil {
		if showDiagnostics {
			fmt.Fprintln(os.Stderr, err)
		}
		switch {
		case errors.Is(err, errExists):
			status = 10
		case errors.Is(err, errNoRelease):
			status = 21
		}
	}
	os.Exit(status)
}

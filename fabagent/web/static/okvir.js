/* Povlacenje okvira po slici umjesto kucanja koordinata.
 *
 * Polja okvir_*, motiv_* i izbaci_* su dijelovi slike (0..1). Covjek ih ne treba
 * racunati: izabere koji okvir crta, povuce misem preko slike, a skripta upise
 * brojeve u ista ta polja. Polja ostaju vidljiva i rucno izmjenjiva.
 *
 * Bez biblioteka. Radi na mis i na dodir.
 */
(function () {
  "use strict";

  var GRUPE = {
    okvir: { prefiks: "okvir", boja: "#0d6efd", naziv: "Okvir oko motiva" },
    motiv: { prefiks: "motiv", boja: "#198754", naziv: "Sigurni motiv" },
    izbaci: { prefiks: "izbaci", boja: "#dc3545", naziv: "Izbaci" }
  };
  var IVICE = ["lijevo", "gore", "desno", "dolje"];

  function polje(prefiks, ivica) {
    return document.getElementById(prefiks + "_" + ivica);
  }

  /** Dio slike (0..1) iz piksela, odsjecen na granice. Izvojeno radi testa. */
  function udio(pixel, duzina) {
    if (!duzina) { return 0; }
    var v = pixel / duzina;
    if (v < 0) { v = 0; }
    if (v > 1) { v = 1; }
    return Math.round(v * 10000) / 10000;
  }

  /** Dva ugla -> {lijevo, gore, desno, dolje} u dijelovima slike. */
  function pravougaonik(x1, y1, x2, y2, sirina, visina) {
    return {
      lijevo: udio(Math.min(x1, x2), sirina),
      gore: udio(Math.min(y1, y2), visina),
      desno: udio(Math.max(x1, x2), sirina),
      dolje: udio(Math.max(y1, y2), visina)
    };
  }

  function premali(r) {
    return (r.desno - r.lijevo) < 0.01 || (r.dolje - r.gore) < 0.01;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { udio: udio, pravougaonik: pravougaonik, premali: premali };
    return;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var fajl = document.getElementById("fajl");
    var platno = document.getElementById("okvir-platno");
    if (!fajl || !platno) { return; }

    var slika = document.getElementById("okvir-slika");
    var sloj = document.getElementById("okvir-sloj");
    var poruka = document.getElementById("okvir-poruka");
    var aktivna = "okvir";
    var crta = false;
    var pocetak = null;

    function izabrana() {
      var r = document.querySelector('input[name="okvir-izbor"]:checked');
      return r ? r.value : "okvir";
    }

    function upisi(grupa, r) {
      IVICE.forEach(function (ivica) {
        var p = polje(GRUPE[grupa].prefiks, ivica);
        if (p) { p.value = r[ivica]; }
      });
    }

    function ocisti(grupa) {
      IVICE.forEach(function (ivica) {
        var p = polje(GRUPE[grupa].prefiks, ivica);
        if (p) { p.value = ""; }
      });
    }

    /** Nacrta sve okvire koji imaju popunjena sva cetiri polja. */
    function iscrtaj() {
      sloj.innerHTML = "";
      Object.keys(GRUPE).forEach(function (grupa) {
        var v = {};
        var potpun = IVICE.every(function (ivica) {
          var p = polje(GRUPE[grupa].prefiks, ivica);
          if (!p || p.value === "") { return false; }
          v[ivica] = parseFloat(p.value);
          return !isNaN(v[ivica]);
        });
        if (!potpun) { return; }
        var d = document.createElement("div");
        d.className = "okvir-box";
        d.style.left = (v.lijevo * 100) + "%";
        d.style.top = (v.gore * 100) + "%";
        d.style.width = ((v.desno - v.lijevo) * 100) + "%";
        d.style.height = ((v.dolje - v.gore) * 100) + "%";
        d.style.borderColor = GRUPE[grupa].boja;
        d.setAttribute("data-naziv", GRUPE[grupa].naziv);
        sloj.appendChild(d);
      });
    }

    function tacka(ev) {
      var r = slika.getBoundingClientRect();
      var t = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
      return { x: t.clientX - r.left, y: t.clientY - r.top, w: r.width, h: r.height };
    }

    function pocni(ev) {
      if (!slika.src) { return; }
      ev.preventDefault();
      aktivna = izabrana();
      pocetak = tacka(ev);
      crta = true;
    }

    function vuci(ev) {
      if (!crta) { return; }
      ev.preventDefault();
      var t = tacka(ev);
      var r = pravougaonik(pocetak.x, pocetak.y, t.x, t.y, t.w, t.h);
      upisi(aktivna, r);
      iscrtaj();
    }

    function pusti(ev) {
      if (!crta) { return; }
      crta = false;
      var t = tacka(ev.changedTouches ? { touches: ev.changedTouches } : ev);
      var r = pravougaonik(pocetak.x, pocetak.y, t.x, t.y, t.w, t.h);
      if (premali(r)) {
        ocisti(aktivna);
        poruka.textContent = "Okvir je premali - povuci veci.";
      } else {
        upisi(aktivna, r);
        poruka.textContent = GRUPE[aktivna].naziv + " je postavljen.";
      }
      iscrtaj();
    }

    fajl.addEventListener("change", function () {
      var f = fajl.files && fajl.files[0];
      if (!f || !/^image\//.test(f.type)) {
        platno.hidden = true;
        return;
      }
      slika.src = URL.createObjectURL(f);
      platno.hidden = false;
      poruka.textContent = "Povuci misem preko slike.";
    });

    slika.addEventListener("mousedown", pocni);
    slika.addEventListener("touchstart", pocni, { passive: false });
    document.addEventListener("mousemove", vuci);
    document.addEventListener("touchmove", vuci, { passive: false });
    document.addEventListener("mouseup", pusti);
    document.addEventListener("touchend", pusti);

    var obrisi = document.getElementById("okvir-obrisi");
    if (obrisi) {
      obrisi.addEventListener("click", function () {
        Object.keys(GRUPE).forEach(ocisti);
        iscrtaj();
        poruka.textContent = "Svi okviri obrisani. Bez okvira se pozadina ne dira.";
      });
    }

    IVICE.forEach(function (ivica) {
      Object.keys(GRUPE).forEach(function (grupa) {
        var p = polje(GRUPE[grupa].prefiks, ivica);
        if (p) { p.addEventListener("change", iscrtaj); }
      });
    });

    iscrtaj();
  });
})();
